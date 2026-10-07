from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PERIODS = [
    ('2016_2019', 2016, 2019),
    ('2020_2022', 2020, 2022),
    ('2023_2025', 2023, 2025),
]
INTERACTIONS = [('growth','valuation'),('growth','momentum'),('growth','stability')]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Test #44 candidate family relationships across time periods.')
    parser.add_argument('--dataset', type=Path, default=Path('reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('reports/v5/v1_diagnostic/time_robustness'))
    parser.add_argument('--horizons-weeks', default='13,26,52')
    return parser.parse_args()

def time_robustness(frame: pd.DataFrame, horizons: tuple[int,...]) -> pd.DataFrame:
    selected = frame.loc[frame['v1_rank_band'].astype(str).eq('top10')].copy()
    selected['decision_date'] = pd.to_datetime(selected['decision_date'], errors='raise')
    rows = []
    for label, start_year, end_year in PERIODS:
        period = selected.loc[selected['decision_date'].dt.year.between(start_year, end_year)].copy()
        for weeks in horizons:
            status = 'fwd_{}w_status'.format(weeks)
            excess_col = 'fwd_{}w_excess_return'.format(weeks)
            period['_excess'] = pd.to_numeric(period[excess_col], errors='coerce')
            mature = period.loc[period[status].astype(str).eq('mature') & period['_excess'].notna()].copy()
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
                lmed, rmed = work[lcol].median(), work[rcol].median()
                hh = work.loc[work[lcol].ge(lmed) & work[rcol].ge(rmed), '_excess']
                hl = work.loc[work[lcol].ge(lmed) & work[rcol].lt(rmed), '_excess']
                rows.append({
                    'period': label,
                    'start_year': start_year,
                    'end_year': end_year,
                    'horizon_weeks': weeks,
                    'interaction': left + '_x_' + right,
                    'high_left_high_right_rows': len(hh),
                    'high_left_low_right_rows': len(hl),
                    'high_left_high_right_mean_excess': float(hh.mean()) if len(hh) else np.nan,
                    'high_left_low_right_mean_excess': float(hl.mean()) if len(hl) else np.nan,
                    'low_right_minus_high_right_with_high_left': float(hl.mean() - hh.mean()) if len(hh) and len(hl) else np.nan,
                })
    return pd.DataFrame(rows)

def main() -> None:
    args = parse_args()
    horizons = tuple(int(v.strip()) for v in args.horizons_weeks.split(',') if v.strip())
    frame = pd.read_csv(args.dataset, low_memory=False)
    result = time_robustness(frame, horizons)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output_dir / 'interaction_time_splits.csv', index=False)
    print('V5 #44 TIME-ROBUSTNESS DIAGNOSTIC')
    print('Hypothesis generation only: YES')
    for name in sorted(result['interaction'].unique()) if not result.empty else []:
        print('  ' + name)
        group=result.loc[result['interaction'].eq(name)].sort_values(['period','horizon_weeks'])
        for row in group.itertuples(index=False):
            print('    {} {:>2}w low-right minus high-right={:+.3%} n={}/{}'.format(
                row.period,int(row.horizon_weeks),row.low_right_minus_high_right_with_high_left,
                int(row.high_left_low_right_rows),int(row.high_left_high_right_rows)))
    print('Output: ' + str(args.output_dir))

if __name__ == '__main__':
    main()
