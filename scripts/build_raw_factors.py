from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.factors import (
    add_financial_health_factors,
    add_growth_factors,
    add_quality_factors,
)
from finance.factors.registry import FACTOR_REGISTRY


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build raw V1 factor values from a weekly PIT research panel."
    )
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/raw_factors_v1.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print(f"Loading weekly panel: {args.panel}", flush=True)
    panel = pd.read_csv(args.panel, low_memory=False)
    print(f"Rows loaded: {len(panel):,}", flush=True)

    factors = add_quality_factors(panel)
    factors = add_financial_health_factors(factors)
    factors = add_growth_factors(factors)

    factor_columns = list(FACTOR_REGISTRY)
    identity_columns = [
        column
        for column in (
            "decision_date",
            "as_of",
            "ticker",
            "cik",
            "company_name",
            "research_ready",
            "price_available",
            "fundamentals_available",
            "price_source",
        )
        if column in factors.columns
    ]
    diagnostic_columns = [
        column
        for column in (
            "growth_lookback_days",
            "growth_lookback_valid",
        )
        if column in factors.columns
    ]

    output = factors[
        identity_columns + factor_columns + diagnostic_columns
    ].copy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)

    print("RAW FACTOR BUILD V1")
    print(f"Rows:          {len(output):,}")
    print(f"Factors:       {len(factor_columns)}")
    print(f"Output:        {args.output}")
    print()
    print("FACTOR COVERAGE")
    for column in factor_columns:
        available = int(output[column].notna().sum())
        share = available / len(output) if len(output) else 0.0
        print(f"{column:38s} {available:9,d}  {share:7.2%}")


if __name__ == "__main__":
    main()
