from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from finance.data.robinhood_market_snapshot import (
    load_robinhood_market_snapshot,
    normalize_robinhood_market_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a captured Robinhood market snapshot for shadow scoring."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument("--max-price-age-minutes", type=float, default=30.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/robinhood_market_snapshot_normalized.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/robinhood_market_snapshot_summary.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = load_robinhood_market_snapshot(args.input)

    print("ROBINHOOD MARKET SNAPSHOT NORMALIZATION", flush=True)
    print(f"Input:                      {args.input}", flush=True)
    print(f"As of:                      {args.as_of}", flush=True)
    print(f"Max price age:              {args.max_price_age_minutes:.1f} minutes", flush=True)

    frame, audit = normalize_robinhood_market_snapshot(
        payload,
        as_of=args.as_of,
        max_price_age_minutes=args.max_price_age_minutes,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    pd.DataFrame([asdict(audit)]).to_csv(args.summary_output, index=False)

    print()
    print("NORMALIZATION COMPLETE", flush=True)
    print(f"Records:                    {audit.records:,}", flush=True)
    print(f"Exact symbol matches:       {audit.exact_symbol_matches:,}", flush=True)
    print(f"Valid prices:               {audit.valid_prices:,}", flush=True)
    print(f"Missing prices:             {audit.missing_prices:,}", flush=True)
    print(f"Inactive instruments:       {audit.inactive_instruments:,}", flush=True)
    print(f"Unresolved symbols:         {audit.unresolved_symbols:,}", flush=True)
    print(f"Stale prices:               {audit.stale_prices:,}", flush=True)
    print(f"Output:                     {args.output}", flush=True)
    print(f"Summary:                    {args.summary_output}", flush=True)


if __name__ == "__main__":
    main()
