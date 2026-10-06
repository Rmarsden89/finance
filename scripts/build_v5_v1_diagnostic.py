from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


FAMILY_COLUMNS = [
    'quality_score',
    'financial_health_score',
    'growth_score',
    'valuation_score',
    'stability_score',
    'momentum_score',
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build V5 #44 V1 diagnostic evidence package.')
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--output-dir', type=Path, default=Path('reports/v5/v1_diagnostic'))
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({'true','1','yes','y'})


def build_live_top25(root: Path, selections: pd.DataFrame) -> pd.DataFrame:
    rows = []
    v1_dates = sorted(selections.loc[selections['model_id'].eq('v1'), 'as_of'].astype(str).unique())
    for as_of in v1_dates:
        score_path = (
            root / 'reports' / 'v2' / 'long_growth_v2_research' / as_of
            / 'ttm_shadow' / 'v2_current_scores.csv'
        )
        if not score_path.exists():
            continue
        frame = pd.read_csv(score_path, low_memory=False)
        required = {'ticker','long_growth_v1_score','top_conviction_eligible'}
        if not required.issubset(frame.columns):
            continue
        frame['ticker'] = frame['ticker'].astype(str).str.strip().str.upper()
        frame['long_growth_v1_score'] = pd.to_numeric(frame['long_growth_v1_score'], errors='coerce')
        eligible = _bool(frame['top_conviction_eligible']) & frame['long_growth_v1_score'].notna()
        ranked = frame.loc[eligible].sort_values(
            ['long_growth_v1_score','ticker'],
            ascending=[False, True],
            kind='mergesort',
        ).head(25).copy()
        ranked['v1_rank'] = range(1, len(ranked) + 1)
        ranked['as_of'] = as_of
        ranked['iso_week'] = pd.Timestamp(as_of).isocalendar().year.astype(str) + '-W' + str(pd.Timestamp(as_of).isocalendar().week).zfill(2)
        ranked['selected_top10'] = ranked['v1_rank'].le(10)
        ranked['rank_band'] = ranked['v1_rank'].apply(lambda value: 'top10' if value <= 10 else 'rank11_25')
        keep = ['as_of','iso_week','ticker','v1_rank','rank_band','selected_top10','long_growth_v1_score']
        keep += [column for column in FAMILY_COLUMNS if column in ranked.columns]
        rows.append(ranked[keep])
    if not rows:
        return pd.DataFrame(columns=['as_of','iso_week','ticker','v1_rank','rank_band','selected_top10','long_growth_v1_score'])
    return pd.concat(rows, ignore_index=True)


def build_live_event_evidence(
    attribution: pd.DataFrame,
    forward: pd.DataFrame,
) -> pd.DataFrame:
    if attribution.empty:
        return attribution.copy()
    result = attribution.copy()
    if forward.empty:
        return result
    observed = forward.loc[forward['status'].eq('observed')].copy()
    if observed.empty:
        return result
    observed['horizon_weeks'] = pd.to_numeric(observed['horizon_weeks'], errors='coerce')
    observed['price_only_return'] = pd.to_numeric(observed['price_only_return'], errors='coerce')
    pivot = observed.pivot_table(
        index=['as_of','model_id','ticker'],
        columns='horizon_weeks',
        values='price_only_return',
        aggfunc='first',
    ).reset_index()
    pivot = pivot.rename(columns={value: 'live_fwd_{}w_return'.format(int(value)) for value in pivot.columns if isinstance(value, (int,float))})
    for model_id, transition in [('v1','v1_to_v2'), ('v2_ttm','v1_to_v2'), ('v3_data_coverage','v2_to_v3')]:
        pass
    # Join by ticker/date to any observed V1 return first; this is evidence about
    # what the changed name subsequently did, not causal proof of a model effect.
    v1 = pivot.loc[pivot['model_id'].eq('v1')].drop(columns=['model_id'])
    result = result.merge(v1, on=['as_of','ticker'], how='left')
    return result


def build_historical_rank_band_summary(history: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for band in ('top10','rank11_25','rank26_50','rank51_plus'):
        subset = history.loc[history['v1_rank_band'].astype(str).eq(band)]
        if subset.empty:
            continue
        row = {'rank_band': band, 'rows': len(subset)}
        for family in FAMILY_COLUMNS:
            if family in subset.columns:
                values = pd.to_numeric(subset[family], errors='coerce')
                row[family + '_mean'] = float(values.mean())
                row[family + '_median'] = float(values.median())
        for weeks in (1,4,13,26,52):
            status = subset['fwd_{}w_status'.format(weeks)].astype(str)
            values = pd.to_numeric(subset['fwd_{}w_excess_return'.format(weeks)], errors='coerce')
            mature = status.eq('mature') & values.notna()
            row['fwd_{}w_mature_rows'.format(weeks)] = int(mature.sum())
            row['fwd_{}w_mean_excess'.format(weeks)] = float(values.loc[mature].mean()) if mature.any() else None
            row['fwd_{}w_median_excess'.format(weeks)] = float(values.loc[mature].median()) if mature.any() else None
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    out = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    selection_path = root / 'reports' / 'model_comparison' / 'selections.csv'
    attribution_path = root / 'reports' / 'model_comparison' / 'attribution' / 'selection_attribution_log.csv'
    forward_path = root / 'reports' / 'model_comparison' / 'selection_forward_returns.csv'
    history_path = root / 'reports' / 'v5' / 'attribution_dataset' / 'v5_historical_attribution_dataset.csv'

    selections = pd.read_csv(selection_path, low_memory=False)
    attribution = pd.read_csv(attribution_path, low_memory=False) if attribution_path.exists() else pd.DataFrame()
    forward = pd.read_csv(forward_path, low_memory=False) if forward_path.exists() else pd.DataFrame()
    history = pd.read_csv(history_path, low_memory=False)

    live_top25 = build_live_top25(root, selections)
    events = build_live_event_evidence(attribution, forward)
    historical = build_historical_rank_band_summary(history)

    live_top25.to_csv(out / 'live_v1_top25.csv', index=False)
    events.to_csv(out / 'live_attribution_with_returns.csv', index=False)
    historical.to_csv(out / 'historical_rank_band_context.csv', index=False)

    v1 = selections.loc[selections['model_id'].eq('v1')].copy()
    persistence = (
        v1.groupby('ticker', as_index=False)
        .agg(selection_events=('as_of','count'), distinct_weeks=('iso_week','nunique'), best_rank=('rank','min'), worst_rank=('rank','max'))
        .sort_values(['distinct_weeks','selection_events','best_rank'], ascending=[False,False,True])
    )
    persistence.to_csv(out / 'live_v1_selection_persistence.csv', index=False)

    summary = {
        'schema_version': 1,
        'hypothesis_generation_only': True,
        'completed_v1_observation_dates': int(v1['as_of'].nunique()),
        'distinct_v1_iso_weeks': int(v1['iso_week'].nunique()),
        'live_top25_dates_reconstructed': int(live_top25['as_of'].nunique()) if not live_top25.empty else 0,
        'live_top25_rows': int(len(live_top25)),
        'attribution_events': int(len(events)),
        'historical_context_rows': int(len(history)),
        'historical_rank_band_summary_rows': int(len(historical)),
        'causality_note': 'Live observations generate hypotheses only; historical context is separate and does not prove causal mechanisms.',
    }
    (out / 'diagnostic_summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    hypotheses = pd.DataFrame(columns=[
        'hypothesis_id','status','observation','hypothesis','supporting_evidence',
        'contradictory_evidence','historical_test_question','target_issue'
    ])
    hypotheses.to_csv(out / 'hypothesis_register.csv', index=False)

    print('V5 #44 V1 DIAGNOSTIC PACKAGE')
    print('Hypothesis generation only: YES')
    print('Live dates / ISO weeks:      {} / {}'.format(summary['completed_v1_observation_dates'], summary['distinct_v1_iso_weeks']))
    print('Live Top-25 dates:           {}'.format(summary['live_top25_dates_reconstructed']))
    print('Attribution events:          {}'.format(summary['attribution_events']))
    print('Historical context rows:     {:,}'.format(summary['historical_context_rows']))
    print('Output:                      ' + str(out))


if __name__ == '__main__':
    main()
