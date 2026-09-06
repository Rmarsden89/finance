from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


RATIO_INPUTS = {
    "return_on_assets": ("net_income", "total_assets"),
    "return_on_equity": ("net_income", "shareholders_equity"),
    "operating_margin": ("operating_income", "revenue"),
    "free_cash_flow_margin": (
        "free_cash_flow",
        "revenue",
    ),
    "liabilities_to_assets": ("total_liabilities", "total_assets"),
    "cash_to_assets": ("cash", "total_assets"),
    "operating_cash_flow_to_liabilities": (
        "operating_cash_flow",
        "total_liabilities",
    ),
}

GROWTH_INPUTS = {
    "revenue_growth_1y": "revenue",
    "net_income_growth_1y": "net_income",
    "operating_income_growth_1y": "operating_income",
    "operating_cash_flow_growth_1y": "operating_cash_flow",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit extreme raw factor observations by showing the exact "
            "underlying accounting inputs used to produce them."
        )
    )
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--tail-quantile",
        type=float,
        default=0.001,
        help="Two-sided tail fraction. 0.001 means p0.1/p99.9.",
    )
    parser.add_argument(
        "--max-per-factor",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reports/factor_validation_v1/factor_extreme_inputs.csv"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 < args.tail_quantile < 0.5:
        raise SystemExit("--tail-quantile must be between 0 and 0.5")

    panel = pd.read_csv(args.panel, low_memory=False)
    factors = pd.read_csv(args.factors, low_memory=False)

    key_columns = ["decision_date", "ticker"]
    for key in key_columns:
        if key not in panel.columns or key not in factors.columns:
            raise ValueError(f"Both files must contain {key}")

    panel["decision_date"] = pd.to_datetime(
        panel["decision_date"],
        errors="coerce",
    )
    factors["decision_date"] = pd.to_datetime(
        factors["decision_date"],
        errors="coerce",
    )

    if "free_cash_flow" not in panel.columns:
        ocf = pd.to_numeric(
            panel.get("operating_cash_flow"),
            errors="coerce",
        )
        capex = pd.to_numeric(
            panel.get("capital_expenditures"),
            errors="coerce",
        )
        panel["free_cash_flow"] = ocf - capex

    merged = factors.merge(
        panel,
        on=key_columns,
        how="left",
        suffixes=("", "_panel"),
        validate="one_to_one",
    )

    merged = merged.sort_values(["ticker", "decision_date"]).copy()

    for source in set(GROWTH_INPUTS.values()):
        numeric = pd.to_numeric(merged.get(source), errors="coerce")
        merged[f"{source}_prior_52w"] = (
            numeric.groupby(merged["ticker"], sort=False).shift(52)
        )

    rows = []
    audit_factors = list(RATIO_INPUTS) + list(GROWTH_INPUTS)

    for factor in audit_factors:
        if factor not in merged.columns:
            continue

        values = pd.to_numeric(merged[factor], errors="coerce")
        finite = values[np.isfinite(values)]
        if finite.empty:
            continue

        lower = finite.quantile(args.tail_quantile)
        upper = finite.quantile(1.0 - args.tail_quantile)
        mask = values.notna() & (
            (values < lower)
            | (values > upper)
        )

        candidates = merged.loc[mask].copy()
        candidates["_distance"] = np.maximum(
            lower - pd.to_numeric(
                candidates[factor],
                errors="coerce",
            ),
            pd.to_numeric(
                candidates[factor],
                errors="coerce",
            ) - upper,
        )
        candidates = candidates.sort_values(
            "_distance",
            ascending=False,
        ).head(args.max_per_factor)

        for row in candidates.itertuples(index=False):
            record = {
                "factor": factor,
                "decision_date": getattr(row, "decision_date"),
                "ticker": getattr(row, "ticker"),
                "company_name": getattr(row, "company_name", ""),
                "factor_value": getattr(row, factor),
                "tail_lower": lower,
                "tail_upper": upper,
            }

            if factor in RATIO_INPUTS:
                numerator, denominator = RATIO_INPUTS[factor]
                record["numerator_name"] = numerator
                record["numerator_value"] = getattr(row, numerator, np.nan)
                record["denominator_name"] = denominator
                record["denominator_value"] = getattr(
                    row,
                    denominator,
                    np.nan,
                )
                denominator_value = pd.to_numeric(
                    pd.Series([record["denominator_value"]]),
                    errors="coerce",
                ).iloc[0]
                record["abs_denominator"] = (
                    abs(denominator_value)
                    if pd.notna(denominator_value)
                    else np.nan
                )
                record["current_value"] = ""
                record["prior_value"] = ""
                record["lookback_days"] = ""
            else:
                source = GROWTH_INPUTS[factor]
                current = getattr(row, source, np.nan)
                prior = getattr(
                    row,
                    f"{source}_prior_52w",
                    np.nan,
                )
                record["numerator_name"] = ""
                record["numerator_value"] = ""
                record["denominator_name"] = ""
                record["denominator_value"] = ""
                record["abs_denominator"] = (
                    abs(prior) if pd.notna(prior) else np.nan
                )
                record["current_value"] = current
                record["prior_value"] = prior
                record["lookback_days"] = getattr(
                    row,
                    "growth_lookback_days",
                    "",
                )

            rows.append(record)

    output = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)

    print("FACTOR EXTREME INPUT AUDIT")
    print(f"Rows:           {len(output):,}")
    print(f"Tail quantile:  {args.tail_quantile:.4f}")
    print()
    if output.empty:
        print("No extreme observations found.")
    else:
        for factor, group in output.groupby("factor"):
            denominator = pd.to_numeric(
                group["abs_denominator"],
                errors="coerce",
            )
            tiny = int((denominator < 1.0).sum())
            very_tiny = int((denominator < 0.01).sum())
            print(
                f"{factor:38s} "
                f"rows={len(group):4d} "
                f"denom<1={tiny:4d} "
                f"denom<0.01={very_tiny:4d}"
            )

    print()
    print(f"Output:         {args.output}")


if __name__ == "__main__":
    main()
