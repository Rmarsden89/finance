from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FAMILIES = ['quality','financial_health','growth','valuation','stability','momentum']


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Analyze family patterns within historical V1 Top-10 selections.')
    parser.add_argument('--dataset', type=Path, default=Path('reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('reports/v5/v1_diagnostic/family_patterns'))
    parser.add_argument('--horizons-weeks', default='4,13,26,52')
    return parser.parse_args()


def summarize_family_patterns(frame: pd.DataFrame, horizons: tuple[int, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = frame.loc[frame['v1_rank_band'].astype(str).eq('top10')].copy()
    summary_rows = []
    quartile_rows = []

    for weeks in horizons:
        status_col = 'fwd_{}w_status'.format(weeks)
        excess_col = 'fwd_{}w_excess_return'.format(weeks)
        excess = pd.to_numeric(selected[excess_col], errors='coerce')
        mature = selected.loc[selected[status_col].astype(str).eq('mature') & excess.notna()].copy()
        mature['_excess'] = pd.to_numeric(mature[excess_col], errors='coerce')
        if mature.empty:
            continue

        median_return = float(mature['_excess'].median())
        mature['_outcome_group'] = np.where(mature['_excess'] >= median_return, 'upper_half', 'lower_half')

        for family in FAMILIES:
            score_col = family + '_score'
            if score_col not in mature.columns:
                continue
            values = pd.to_numeric(mature[score_col], errors='coerce')
            valid = mature.loc[values.notna()].copy()
            valid['_family_score'] = pd.to_numeric(valid[score_col], errors='coerce')
            if valid.empty:
                continue

            upper = valid.loc[valid['_outcome_group'].eq('upper_half'), '_family_score']
            lower = valid.loc[valid['_outcome_group'].eq('lower_half'), '_family_score']
            corr = valid[['_family_score','_excess']].corr(method='spearman').iloc[0,1] if len(valid) >= 3 else np.nan
            summary_rows.append({
                'horizon_weeks': weeks,
                'family': family,
                'mature_rows': len(mature),
                'family_rows': len(valid),
                'upper_half_mean_score': float(upper.mean()) if not upper.empty else np.nan,
                'lower_half_mean_score': float(lower.mean()) if not lower.empty else np.nan,
                'upper_minus_lower_mean_score': float(upper.mean() - lower.mean()) if not upper.empty and not lower.empty else np.nan,
                'spearman_score_vs_excess_return': float(corr) if pd.notna(corr) else np.nan,
            })

            if valid['_family_score'].nunique() >= 4:
                valid['_quartile'] = pd.qcut(valid['_family_score'], 4, labels=False, duplicates='drop')
                for quartile, group in valid.groupby('_quartile', dropna=True):
                    quartile_rows.append({
                        'horizon_weeks': weeks,
                        'family': family,
                        'score_quartile': int(quartile) + 1,
                        'rows': len(group),
                        'mean_excess_return': float(group['_excess'].mean()),
                        'median_excess_return': float(group['_excess'].median()),
                        'positive_excess_rate': float(group['_excess'].gt(0).mean()),
                        'mean_family_score': float(group['_family_score'].mean()),
                    })

    return pd.DataFrame(summary_rows), pd.DataFrame(quartile_rows)


def build_candidate_findings(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if summary.empty:
        return pd.DataFrame(columns=['family','consistent_direction','positive_horizons','negative_horizons','median_spearman','interpretation'])
    for family, group in summary.groupby('family'):
        corr = pd.to_numeric(group['spearman_score_vs_excess_return'], errors='coerce').dropna()
        positive = int(corr.gt(0).sum())
        negative = int(corr.lt(0).sum())
        consistent = 'positive' if positive == len(corr) and len(corr) else ('negative' if negative == len(corr) and len(corr) else 'mixed')
        rows.append({
            'family': family,
            'consistent_direction': consistent,
            'positive_horizons': positive,
            'negative_horizons': negative,
            'median_spearman': float(corr.median()) if not corr.empty else np.nan,
            'interpretation': (
                'candidate upside association' if consistent == 'positive' else
                'candidate inverse/risk association' if consistent == 'negative' else
                'mixed horizon relationship'
            ),
        })
    return pd.DataFrame(rows).sort_values(['consistent_direction','median_spearman'], ascending=[True,False])


def main() -> None:
    args = parse_args()
    horizons = tuple(int(value.strip()) for value in args.horizons_weeks.split(',') if value.strip())
    frame = pd.read_csv(args.dataset, low_memory=False)
    required = {'v1_rank_band'}
    required.update('fwd_{}w_status'.format(w) for w in horizons)
    required.update('fwd_{}w_excess_return'.format(w) for w in horizons)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit('Family-pattern input missing columns: ' + ', '.join(missing))

    summary, quartiles = summarize_family_patterns(frame, horizons)
    findings = build_candidate_findings(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / 'family_outcome_summary.csv', index=False)
    quartiles.to_csv(args.output_dir / 'family_score_quartiles.csv', index=False)
    findings.to_csv(args.output_dir / 'family_candidate_findings.csv', index=False)

    payload = {
        'schema_version': 1,
        'hypothesis_generation_only': True,
        'selected_rows': int(frame['v1_rank_band'].astype(str).eq('top10').sum()),
        'horizons_weeks': list(horizons),
        'family_rows': int(len(summary)),
        'families': findings.to_dict(orient='records'),
    }
    (args.output_dir / 'summary.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    print('V5 #44 FAMILY-PATTERN DIAGNOSTIC')
    print('Hypothesis generation only: YES')
    print('Historical Top-10 rows:      {:,}'.format(payload['selected_rows']))
    print('FAMILY SCORE / EXCESS-RETURN SPEARMAN')
    for family in FAMILIES:
        group = summary.loc[summary['family'].eq(family)].sort_values('horizon_weeks')
        if group.empty:
            continue
        values = ', '.join('{}w={:+.3f}'.format(int(row.horizon_weeks), row.spearman_score_vs_excess_return) for row in group.itertuples(index=False))
        print('  {:18s} {}'.format(family, values))
    print('CANDIDATE FINDINGS')
    for row in findings.itertuples(index=False):
        print('  {:18s} {:8s} median rho={:+.3f}'.format(row.family, row.consistent_direction, row.median_spearman))
    print('Output:                      ' + str(args.output_dir))


if __name__ == '__main__':
    main()
