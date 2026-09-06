from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.factors import (
    add_financial_health_factors,
    add_growth_factors,
    add_quality_factors,
    validate_raw_factors,
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
    factors = validate_raw_factors(factors)

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
    input_columns = [
        column
        for column in (
            "revenue",
            "net_income",
            "operating_income",
            "total_assets",
            "total_liabilities",
            "shareholders_equity",
            "cash",
            "operating_cash_flow",
            "capital_expenditures",
            "revenue_prior_52w",
            "net_income_prior_52w",
            "operating_income_prior_52w",
            "operating_cash_flow_prior_52w",
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
    validation_columns = []
    for factor in factor_columns:
        for suffix in (
            "_valid",
            "_invalid_reason",
            "_validated",
        ):
            column = f"{factor}{suffix}"
            if column in factors.columns:
                validation_columns.append(column)

    output = factors[
        identity_columns
        + input_columns
        + factor_columns
        + diagnostic_columns
        + validation_columns
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
        validated = int(output[f"{column}_validated"].notna().sum())
        raw_share = available / len(output) if len(output) else 0.0
        valid_share = validated / len(output) if len(output) else 0.0
        rejected = available - validated
        print(
            f"{column:38s} "
            f"raw={raw_share:7.2%} "
            f"validated={valid_share:7.2%} "
            f"rejected={rejected:6,d}"
        )


if __name__ == "__main__":
    main()
