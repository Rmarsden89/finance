from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import BacktestConfig, BacktestPriceStore, run_ranked_accumulation_backtest

EXPECTED_XIRR = 0.2521
EXPECTED_TERMINAL_VALUE = 19632.82
XIRR_TOLERANCE = 0.00015
TERMINAL_VALUE_TOLERANCE = 1.00

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser(description='Reproduce frozen V1 from the V5 attribution dataset.')
    parser.add_argument('--dataset', type=Path, default=Path('reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv'))
    parser.add_argument('--prices', type=Path, default=Path('data/market/daily_prices.csv.gz'))
    parser.add_argument('--output', type=Path, default=Path('reports/v5/attribution_dataset/closeout.json'))
    args = parser.parse_args()

    frame = pd.read_csv(args.dataset, low_memory=False)
    duplicate_keys = int(frame[['decision_date', 'ticker']].duplicated().sum())
    if duplicate_keys:
        raise SystemExit('Duplicate decision_date/ticker keys: ' + str(duplicate_keys))

    result = run_ranked_accumulation_backtest(
        frame,
        price_store=BacktestPriceStore(args.prices),
        model_id='long_growth_v1_v5_reproduction',
        score_column='long_growth_v1_score',
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag='top_conviction_eligible',
            max_addon_position_weight=0.10,
        ),
        start=date(2016, 1, 1),
        end=date(2025, 12, 31),
    )

    xirr = float(result.summary['xirr'])
    terminal = float(result.summary['terminal_value'])
    xirr_delta = xirr - EXPECTED_XIRR
    terminal_delta = terminal - EXPECTED_TERMINAL_VALUE
    passed = abs(xirr_delta) <= XIRR_TOLERANCE and abs(terminal_delta) <= TERMINAL_VALUE_TOLERANCE

    payload = {
        'schema_version': 1,
        'research_only': True,
        'dataset_sha256': sha256_file(args.dataset),
        'prices_sha256': sha256_file(args.prices),
        'rows': int(len(frame)),
        'decision_dates': int(pd.to_datetime(frame['decision_date']).nunique()),
        'duplicate_keys': duplicate_keys,
        'expected_xirr': EXPECTED_XIRR,
        'actual_xirr': xirr,
        'xirr_delta': xirr_delta,
        'expected_terminal_value': EXPECTED_TERMINAL_VALUE,
        'actual_terminal_value': terminal,
        'terminal_value_delta': terminal_delta,
        'max_drawdown': float(result.summary['max_drawdown']),
        'decision_weeks': int(result.summary['decision_weeks']),
        'status': 'PASS' if passed else 'FAIL',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    print('V5 ATTRIBUTION DATASET CLOSEOUT')
    print('Research only:             YES')
    print('Status:                    ' + payload['status'])
    print('V1 XIRR:                   {:.4%}'.format(xirr))
    print('Expected checkpoint:       {:.4%}'.format(EXPECTED_XIRR))
    print('XIRR delta:                {:+.6%}'.format(xirr_delta))
    print('Terminal value:            ${:,.2f}'.format(terminal))
    print('Expected checkpoint:       ${:,.2f}'.format(EXPECTED_TERMINAL_VALUE))
    print('Terminal delta:            ${:+,.2f}'.format(terminal_delta))
    print('Max drawdown:              {:.2%}'.format(float(result.summary['max_drawdown'])))
    print('Decision weeks:            {:,}'.format(int(result.summary['decision_weeks'])))
    print('Dataset SHA-256:           ' + payload['dataset_sha256'])
    print('Output:                    ' + str(args.output))
    if not passed:
        raise SystemExit('V5 attribution dataset closeout failed closed.')

if __name__ == '__main__':
    main()
