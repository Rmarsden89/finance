from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Analyze V5 #44 diagnostic evidence and generate bounded hypotheses.')
    parser.add_argument('--input-dir', type=Path, default=Path('reports/v5/v1_diagnostic'))
    parser.add_argument('--output-dir', type=Path, default=Path('reports/v5/v1_diagnostic/analysis'))
    return parser.parse_args()


def _safe_read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False) if path.exists() else pd.DataFrame()


def historical_rank_band_deltas(context: pd.DataFrame) -> pd.DataFrame:
    if context.empty:
        return pd.DataFrame()
    indexed = context.set_index('rank_band')
    if 'top10' not in indexed.index or 'rank11_25' not in indexed.index:
        return pd.DataFrame()
    rows = []
    for weeks in (1,4,13,26,52):
        col = 'fwd_{}w_mean_excess'.format(weeks)
        med = 'fwd_{}w_median_excess'.format(weeks)
        if col not in context.columns:
            continue
        top = indexed.loc['top10']
        near = indexed.loc['rank11_25']
        rows.append({
            'horizon_weeks': weeks,
            'top10_mean_excess': top.get(col),
            'rank11_25_mean_excess': near.get(col),
            'near_minus_top10_mean_excess': near.get(col) - top.get(col),
            'top10_median_excess': top.get(med),
            'rank11_25_median_excess': near.get(med),
            'near_minus_top10_median_excess': near.get(med) - top.get(med),
            'top10_mature_rows': int(top.get('fwd_{}w_mature_rows'.format(weeks), 0)),
            'rank11_25_mature_rows': int(near.get('fwd_{}w_mature_rows'.format(weeks), 0)),
        })
    return pd.DataFrame(rows)


def attribution_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or 'reason' not in events.columns:
        return pd.DataFrame(columns=['transition','direction','reason','events'])
    return (
        events.groupby(['transition','direction','reason'], dropna=False)
        .size()
        .reset_index(name='events')
        .sort_values(['events','transition','direction'], ascending=[False,True,True])
    )


def persistence_summary(persistence: pd.DataFrame, distinct_weeks: int) -> dict:
    if persistence.empty:
        return {'persistent_names': [], 'one_week_names': [], 'max_distinct_weeks': 0}
    work = persistence.copy()
    work['distinct_weeks'] = pd.to_numeric(work['distinct_weeks'], errors='coerce').fillna(0).astype(int)
    persistent = work.loc[work['distinct_weeks'] >= max(2, distinct_weeks - 1), 'ticker'].astype(str).tolist()
    one_week = work.loc[work['distinct_weeks'].eq(1), 'ticker'].astype(str).tolist()
    return {
        'persistent_names': persistent,
        'one_week_names': one_week,
        'max_distinct_weeks': int(work['distinct_weeks'].max()),
    }


def live_return_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    return_cols = [column for column in events.columns if column.startswith('live_fwd_') and column.endswith('_return')]
    rows = []
    for col in return_cols:
        values = pd.to_numeric(events[col], errors='coerce')
        observed = values.dropna()
        rows.append({
            'return_field': col,
            'observed_events': int(observed.size),
            'mean_return': float(observed.mean()) if not observed.empty else None,
            'median_return': float(observed.median()) if not observed.empty else None,
        })
    return pd.DataFrame(rows)


def hypothesis_rows(rank_deltas: pd.DataFrame, attr: pd.DataFrame, persistence: dict, live_returns: pd.DataFrame) -> pd.DataFrame:
    rows = []

    if not rank_deltas.empty:
        positive = rank_deltas.loc[pd.to_numeric(rank_deltas['near_minus_top10_mean_excess'], errors='coerce') > 0]
        negative = rank_deltas.loc[pd.to_numeric(rank_deltas['near_minus_top10_mean_excess'], errors='coerce') <= 0]
        if not positive.empty:
            horizons = ','.join(str(int(value)) for value in positive['horizon_weeks'])
            contradiction = 'Near-miss does not outperform at horizons: ' + (
                ','.join(str(int(value)) for value in negative['horizon_weeks']) if not negative.empty else 'none in current summary'
            )
            rows.append({
                'hypothesis_id':'H44-01',
                'status':'supported_for_historical_testing',
                'observation':'Ranks 11-25 have higher mean SPY-relative returns than Top 10 at horizon(s): ' + horizons,
                'hypothesis':'V1 may have useful signal near the selection boundary but imperfect final rank ordering.',
                'supporting_evidence':'Historical rank-band comparison from PIT-safe #45 dataset.',
                'contradictory_evidence':contradiction,
                'historical_test_question':'Which factor/family patterns distinguish profitable ranks 11-25 from selected Top-10 names?',
                'target_issue':'#47',
            })

    if not attr.empty:
        ttm_events = int(attr.loc[attr['reason'].astype(str).str.contains('ttm_', na=False), 'events'].sum())
        recovery_events = int(attr.loc[attr['reason'].astype(str).str.contains('v3|recovery|displacement', case=False, regex=True, na=False), 'events'].sum())
        if ttm_events > 0:
            rows.append({
                'hypothesis_id':'H44-02',
                'status':'supported_for_historical_testing',
                'observation':'Live V1/V2 differences repeatedly arise from TTM eligibility/rank changes ({} events).'.format(ttm_events),
                'hypothesis':'Annual-vs-TTM valuation methodology materially affects the Top-10 boundary and deserves attribution against forward returns.',
                'supporting_evidence':'Saved V1-to-V2 attribution events.',
                'contradictory_evidence':'Live sample is only four independent weeks; this does not establish superiority.',
                'historical_test_question':'Do TTM-driven entrants/displacements improve terminal value and forward excess returns consistently across historical periods?',
                'target_issue':'#47',
            })
        if recovery_events > 0:
            rows.append({
                'hypothesis_id':'H44-03',
                'status':'supported_for_historical_testing',
                'observation':'V2/V3 membership changes include data-recovery and indirect rerank effects ({} events).'.format(recovery_events),
                'hypothesis':'Coverage gaps can alter selections independently of model architecture and must be separated from true factor-model effects.',
                'supporting_evidence':'Saved V2-to-V3 attribution events.',
                'contradictory_evidence':'V3 changes are primarily data-quality changes, not evidence for a new V5 scoring rule.',
                'historical_test_question':'When coverage is held constant, which remaining V1 selection errors are attributable to scoring architecture rather than missing data?',
                'target_issue':'#47',
            })

    if persistence.get('persistent_names'):
        rows.append({
            'hypothesis_id':'H44-04',
            'status':'supported_for_historical_testing',
            'observation':'Several names persist in V1 Top-10 across nearly all independent live weeks: ' + ', '.join(persistence['persistent_names']),
            'hypothesis':'Persistent selections may represent strong consensus signal, while transient selections may contain more boundary noise.',
            'supporting_evidence':'Live V1 Top-10 persistence across distinct ISO weeks.',
            'contradictory_evidence':'Persistence alone can reflect stale or slow-moving fundamentals and does not prove higher return.',
            'historical_test_question':'Does historical rank persistence predict better subsequent returns, or merely lower turnover?',
            'target_issue':'#47/#48',
        })

    if not live_returns.empty and int(live_returns['observed_events'].sum()) == 0:
        rows.append({
            'hypothesis_id':'H44-05',
            'status':'insufficient_evidence',
            'observation':'No matured live forward-return events are yet available in the diagnostic join.',
            'hypothesis':'Recent live winners/losers cannot yet be used as return evidence.',
            'supporting_evidence':'Forward-return maturity status.',
            'contradictory_evidence':'Historical forward-return evidence is available separately and remains the proper test source.',
            'historical_test_question':'Defer live-return conclusions until horizons mature; use historical PIT evidence meanwhile.',
            'target_issue':'#44 monitoring',
        })

    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    inp = args.input_dir
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    context = _safe_read(inp / 'historical_rank_band_context.csv')
    events = _safe_read(inp / 'live_attribution_with_returns.csv')
    persistence_df = _safe_read(inp / 'live_v1_selection_persistence.csv')
    summary_path = inp / 'diagnostic_summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))

    rank_deltas = historical_rank_band_deltas(context)
    attr = attribution_summary(events)
    persistence = persistence_summary(persistence_df, int(summary['distinct_v1_iso_weeks']))
    live_returns = live_return_summary(events)
    hypotheses = hypothesis_rows(rank_deltas, attr, persistence, live_returns)

    rank_deltas.to_csv(out / 'historical_rank_band_deltas.csv', index=False)
    attr.to_csv(out / 'live_attribution_reason_summary.csv', index=False)
    live_returns.to_csv(out / 'live_return_maturity_summary.csv', index=False)
    hypotheses.to_csv(inp / 'hypothesis_register.csv', index=False)

    result = {
        'schema_version':1,
        'hypothesis_generation_only':True,
        'hypotheses_generated':int(len(hypotheses)),
        'persistent_names':persistence['persistent_names'],
        'one_week_names':persistence['one_week_names'],
        'historical_rank_band_comparisons':int(len(rank_deltas)),
        'attribution_reason_groups':int(len(attr)),
        'live_return_fields':int(len(live_returns)),
    }
    (out / 'analysis_summary.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    print('V5 #44 V1 DIAGNOSTIC ANALYSIS')
    print('Hypothesis generation only: YES')
    print('Hypotheses generated:        {}'.format(result['hypotheses_generated']))
    print('Persistent names:            ' + (', '.join(result['persistent_names']) or 'none'))
    print('One-week names:              ' + (', '.join(result['one_week_names']) or 'none'))
    if not rank_deltas.empty:
        print('NEAR-MISS VS TOP-10 MEAN EXCESS RETURN')
        for row in rank_deltas.itertuples(index=False):
            print('  {:>2}w: {:+.4%}'.format(int(row.horizon_weeks), float(row.near_minus_top10_mean_excess)))
    if not attr.empty:
        print('ATTRIBUTION REASONS')
        for row in attr.itertuples(index=False):
            print('  {} / {} / {}: {}'.format(row.transition, row.direction, row.reason, int(row.events)))
    print('Output:                      ' + str(out))


if __name__ == '__main__':
    main()
