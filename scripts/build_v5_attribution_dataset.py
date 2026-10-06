from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.backtest import BacktestPriceStore
from finance.research.v5_attribution import (
    DEFAULT_HORIZONS_WEEKS,
    build_v5_attribution_dataset,
    sha256_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the V5 historical factor/family attribution research dataset."
    )
    parser.add_argument(
        "--long-growth",
        type=Path,
        default=Path("reports/long_growth_v1.csv"),
        help="Historical fully-scored V1 panel.",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument(
        "--benchmark-prices",
        type=Path,
        default=Path("data/market/benchmark_spy.csv"),
    )
    parser.add_argument(
        "--horizons-weeks",
        default=",".join(str(value) for value in DEFAULT_HORIZONS_WEEKS),
    )
    parser.add_argument("--max-exit-delay-days", type=int, default=7)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/v5/attribution_dataset"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    horizons = tuple(
        int(value.strip())
        for value in args.horizons_weeks.split(",")
        if value.strip()
    )
    if not horizons or any(value <= 0 for value in horizons):
        raise SystemExit("--horizons-weeks must contain positive integers")
    if args.max_exit_delay_days < 0:
        raise SystemExit("--max-exit-delay-days must be nonnegative")

    frame = pd.read_csv(args.long_growth, low_memory=False)
    store = BacktestPriceStore(args.prices)
    benchmark_store = BacktestPriceStore(
        args.benchmark_prices,
        ticker_column="ticker",
    )

    dataset, summary = build_v5_attribution_dataset(
        frame,
        price_store=store,
        benchmark_price_store=benchmark_store,
        horizons_weeks=horizons,
        max_exit_delay_days=args.max_exit_delay_days,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.output_dir / "v5_historical_attribution_dataset.csv"
    summary_path = args.output_dir / "summary.json"

    dataset.to_csv(dataset_path, index=False)
    payload = {
        "schema_version": 1,
        "research_only": True,
        "source_model": "long_growth_v1",
        "rows": summary.rows,
        "decision_dates": summary.decision_dates,
        "unique_tickers": summary.unique_tickers,
        "top_conviction_rows": summary.top_conviction_rows,
        "ranked_rows": summary.ranked_rows,
        "duplicate_keys": summary.duplicate_keys,
        "horizons_weeks": list(summary.horizons_weeks),
        "max_exit_delay_days": args.max_exit_delay_days,
        "input_fingerprints": {
            "long_growth_sha256": sha256_file(args.long_growth),
            "prices_sha256": sha256_file(args.prices),
            "benchmark_prices_sha256": sha256_file(args.benchmark_prices),
        },
        "output": str(dataset_path),
    }
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 HISTORICAL ATTRIBUTION DATASET")
    print("Research only:             YES")
    print(f"Rows:                      {summary.rows:,}")
    print(f"Decision dates:            {summary.decision_dates:,}")
    print(f"Unique tickers:            {summary.unique_tickers:,}")
    print(f"Ranked rows:               {summary.ranked_rows:,}")
    print(f"Duplicate keys:            {summary.duplicate_keys}")
    print(f"Forward horizons (weeks):  {','.join(map(str, horizons))}")
    print(f"Dataset:                   {dataset_path}")
    print(f"Summary:                   {summary_path}")


if __name__ == "__main__":
    main()
