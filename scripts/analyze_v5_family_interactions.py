from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FAMILIES = ['quality','financial_health','growth','valuation','stability','momentum']
INTERACTIONS = [
    ('growth','valuation'),
    ('growth','momentum'),
    ('quality','momentum'),
    ('growth','stability'),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Analyze upside/downside and selected family interactions within V1 Top-10 history.')
    parser.add_argument('--dataset', type=Path, default=Path('reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('reports/v5/v1_diagnostic/family_interactions'))
    parser.add_argument('--horizons-weeks', default='13,26,52')
    return parser.parse_args()


def family_tail_summary(frame: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    selected = frame.loc[frame['v1_rank_band'].astype(str).eq('top10')].copy()
    rows = []
    for weeks in horizons:
        status_col = 'fwd_{}w_status'.format(weeks)
        excess_col = 'fwd_{}w_excess_return'.format(weeks)
        selected['_excess'] = pd.to_numeric(selected[excess_col], errors='coerce')
        mature = selected.loc[selected[status_col].astype(str).eq('mature') & selected['_excess'].notna()].copy()
        for family in FAMILIES:
            col = family + '_score'
            if col not in mature.columns:
                continue
            mature['_score'] = pd.to_numeric(mature[col], errors='coerce')
            valid = mature.loc[mature['_score'].notna()].copy()
            if valid['_score'].nunique() < 4:
                continue
            valid['_q'] = pd.qcut(valid['_score'], 4, labels=False, duplicates='drop')
            low = valid.loc[valid['_q'].eq(valid['_q'].min()), '_excess']
            high = valid.loc[valid['_q'].eq(valid['_q'].max()), '_excess']
            rows.append({
                'horizon_weeks': weeks,
                'family': family,
                'rows': len(valid),
                'high_minus_low_mean_excess': float(high.mean() - low.mean()),
                'high_minus_low_median_excess': float(high.median() - low.median()),
                'high_q_positive_excess_rate': float(high.gt(0).mean()),
                'low_q_positive_excess_rate': float(low.gt(0).mean()),
                'high_q_loss_rate': float(high.lt(0).mean()),
                'low_q_loss_rate': float(low.lt(0).mean()),
                'high_q_p10_excess': float(high.quantile(0.10)),
                'low_q_p10_excess': float(low.quantile(0.10)),
                'p10_protection_high_minus_low': float(high.quantile(0.10) - low.quantile(0.10)),
            })
    return pd.DataFrame(rows)


def interaction_summary(frame: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    selected = frame.loc[frame['v1_rank_band'].astype(str).eq('top10')].copy()
    rows = []
    for weeks in horizons:
        status_col = 'fwd_{}w_status'.format(weeks)
        excess_col = 'fwd_{}w_excess_return'.format(weeks)
        selected['_excess'] = pd.to_numeric(selected[excess_col], errors='coerce')
        mature = selected.loc[selected[status_col].astype(str).eq('mature') & selected['_excess'].notna()].copy()
        for left, right in INTERACTIONS:
            lcol, rcol = left + '_score', right + '_score'
            if lcol not in mature.columns or rcol not in mature.columns:
                continue
            work = mature[[lcol,rcol,'_excess']].copy()
            work[lcol] = pd.to_numeric(work[lcol], errors='coerce')
            work[rcol] = pd.to_numeric(work[rcol], errors='coerce')
            work = work.dropna()
            if len(work) < 40:
                continue
            lmed = work[lcol].median()
            rmed = work[rcol].median()
            work['left_band'] = np.where(work[lcol] >= lmed, 'high', 'low')
            work['right_band'] = np.where(work[rcol] >= rmed, 'high', 'low')
            for (lb, rb), group in work.groupby(['left_band','right_band']):
                rows.append({
                    'horizon_weeks': weeks,
                    'interaction': left + '_x_' + right,
                    'left_family': left,
                    'right_family': right,
                    'left_band': lb,
                    'right_band': rb,
                    'rows': len(group),
                    'mean_excess_return': float(group['_excess'].mean()),
                    'median_excess_return': float(group['_excess'].median()),
                    'positive_excess_rate': float(group['_excess'].gt(0).mean()),
                    'p10_excess_return': float(group['_excess'].quantile(0.10)),
                })
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    horizons = tuple(int(v.strip()) for v in args.horizons_weeks.split(',') if v.strip())
    frame = pd.read_csv(args.dataset, low_memory=False)
    tails = family_tail_summary(frame, horizons)
    interactions = interaction_summary(frame, horizons)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tails.to_csv(args.output_dir / 'family_tail_summary.csv', index=False)
    interactions.to_csv(args.output_dir / 'interaction_summary.csv', index=False)

    print('V5 #44 FAMILY TAIL / INTERACTION DIAGNOSTIC')
    print('Hypothesis generation only: YES')
    print('FAMILY HIGH-Q MINUS LOW-Q MEAN EXCESS / P10 PROTECTION')
    for row in tails.itertuples(index=False):
        print('  {:>2}w {:18s} mean={:+.3%} p10={:+.3%}'.format(
            int(row.horizon_weeks), row.family, row.high_minus_low_mean_excess, row.p10_protection_high_minus_low
        ))
    print('INTERACTION CELLS')
    for name in sorted(interactions['interaction'].unique()) if not interactions.empty else []:
        print('  ' + name)
        group = interactions.loc[interactions['interaction'].eq(name)].sort_values(['horizon_weeks','left_band','right_band'])
        for row in group.itertuples(index=False):
            print('    {:>2}w {}/{} mean={:+.3%} p10={:+.3%} n={}'.format(
                int(row.horizon_weeks), row.left_band, row.right_band, row.mean_excess_return, row.p10_excess_return, int(row.rows)
            ))
    print('Output: ' + str(args.output_dir))


if __name__ == '__main__':
    main()
