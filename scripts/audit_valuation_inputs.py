from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit V1 valuation inputs, market-cap scale, and annual fact age."
    )
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/valuation_validation_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.factors, low_memory=False)
    frame["decision_timestamp"] = pd.to_datetime(
        frame["as_of"] if "as_of" in frame.columns else frame["decision_date"],
        errors="coerce",
        utc=True,
    ).dt.tz_convert(None)

    market_cap = pd.to_numeric(frame.get("market_cap"), errors="coerce")
    assets = pd.to_numeric(frame.get("total_assets"), errors="coerce")
    annual_revenue = pd.to_numeric(frame.get("annual_revenue"), errors="coerce")

    cap_to_assets = market_cap / assets.where(assets > 0)
    cap_to_sales = market_cap / annual_revenue.where(annual_revenue > 0)

    summary_rows = []
    for name, values in {
        "market_cap_to_assets": cap_to_assets,
        "market_cap_to_annual_revenue": cap_to_sales,
    }.items():
        finite = values[np.isfinite(values)]
        summary_rows.append({
            "metric": name,
            "available": int(values.notna().sum()),
            "coverage_pct": values.notna().mean(),
            "min": finite.min() if not finite.empty else np.nan,
            "p001": finite.quantile(0.001) if not finite.empty else np.nan,
            "p01": finite.quantile(0.01) if not finite.empty else np.nan,
            "median": finite.median() if not finite.empty else np.nan,
            "p99": finite.quantile(0.99) if not finite.empty else np.nan,
            "p999": finite.quantile(0.999) if not finite.empty else np.nan,
            "max": finite.max() if not finite.empty else np.nan,
        })

    age_rows = []
    for concept in (
        "annual_revenue",
        "annual_net_income",
        "annual_operating_cash_flow",
        "annual_capital_expenditures",
    ):
        accepted_col = f"{concept}_accepted_at"
        if accepted_col not in frame.columns:
            continue
        accepted = pd.to_datetime(
            frame[accepted_col],
            errors="coerce",
            utc=True,
        ).dt.tz_convert(None)
        age = (frame["decision_timestamp"] - accepted).dt.days
        finite = age.dropna()
        age_rows.append({
            "concept": concept,
            "available": int(age.notna().sum()),
            "median_age_days": finite.median() if not finite.empty else np.nan,
            "p95_age_days": finite.quantile(0.95) if not finite.empty else np.nan,
            "max_age_days": finite.max() if not finite.empty else np.nan,
            "future_rows": int((age < 0).sum()),
            "over_550_days": int((age > 550).sum()),
        })

    suspicious_columns = [
        column
        for column in (
            "decision_date",
            "as_of",
            "ticker",
            "company_name",
            "close",
            "shares_outstanding",
            "market_cap",
            "total_assets",
            "annual_revenue",
            "earnings_yield_annual_valid",
            "earnings_yield_annual_invalid_reason",
            "sales_yield_annual_valid",
            "sales_yield_annual_invalid_reason",
            "free_cash_flow_yield_annual_valid",
            "free_cash_flow_yield_annual_invalid_reason",
            "book_to_market_valid",
            "book_to_market_invalid_reason",
        )
        if column in frame.columns
    ]

    suspicious = frame.loc[
        market_cap.notna()
        & (
            (cap_to_assets.notna() & ((cap_to_assets < 0.001) | (cap_to_assets > 1000)))
            | (
                cap_to_sales.notna()
                & ((cap_to_sales < 0.001) | (cap_to_sales > 1000))
            )
        ),
        suspicious_columns,
    ].copy()

    suspicious["market_cap_to_assets"] = cap_to_assets.loc[suspicious.index]
    suspicious["market_cap_to_annual_revenue"] = cap_to_sales.loc[suspicious.index]
    suspicious = suspicious.sort_values(
        ["ticker", "decision_date"],
        kind="stable",
    )

    quarantine_rows = []
    valuation_factors = (
        "earnings_yield_annual",
        "sales_yield_annual",
        "free_cash_flow_yield_annual",
        "book_to_market",
    )
    suspicious_mask = market_cap.notna() & (
        (
            cap_to_assets.notna()
            & ((cap_to_assets < 0.001) | (cap_to_assets > 1000))
        )
        | (
            cap_to_sales.notna()
            & ((cap_to_sales < 0.001) | (cap_to_sales > 1000))
        )
    )

    for factor in valuation_factors:
        valid_col = f"{factor}_valid"
        reason_col = f"{factor}_invalid_reason"
        if valid_col not in frame.columns:
            continue

        affected = frame.loc[suspicious_mask & frame[factor].notna()]
        rejected = affected.loc[~affected[valid_col].fillna(False).astype(bool)]
        scale_reason = (
            rejected[reason_col]
            .fillna("")
            .astype(str)
            .str.contains("market_cap_scale_mismatch", regex=False)
        ) if reason_col in rejected.columns else pd.Series(False, index=rejected.index)

        quarantine_rows.append({
            "factor": factor,
            "suspicious_raw_rows_with_factor": len(affected),
            "rejected_rows": len(rejected),
            "rejected_pct": len(rejected) / len(affected) if len(affected) else 0.0,
            "market_cap_scale_reason_rows": int(scale_reason.sum()),
        })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        args.output_dir / "valuation_scale_summary.csv",
        index=False,
    )
    pd.DataFrame(age_rows).to_csv(
        args.output_dir / "valuation_annual_age_summary.csv",
        index=False,
    )
    suspicious.to_csv(
        args.output_dir / "valuation_scale_suspicious.csv",
        index=False,
    )
    pd.DataFrame(quarantine_rows).to_csv(
        args.output_dir / "valuation_scale_quarantine_summary.csv",
        index=False,
    )

    print("VALUATION INPUT AUDIT V1")
    print(f"Rows:                  {len(frame):,}")
    print(f"Market cap available:  {market_cap.notna().mean():7.2%}")
    print(f"Suspicious scale rows: {len(suspicious):,}")
    print(f"Reports:               {args.output_dir}")


if __name__ == "__main__":
    main()
