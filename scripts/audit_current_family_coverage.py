from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


FAMILIES = {
    "quality": [
        "return_on_assets",
        "return_on_equity",
        "operating_margin",
        "free_cash_flow_margin",
    ],
    "financial_health": [
        "liabilities_to_assets",
        "cash_to_assets",
        "operating_cash_flow_to_liabilities",
    ],
    "growth": [
        "revenue_growth_1y",
        "net_income_growth_1y",
        "operating_income_growth_1y",
        "operating_cash_flow_growth_1y",
    ],
    "valuation": [
        "earnings_yield_annual",
        "sales_yield_annual",
        "free_cash_flow_yield_annual",
        "book_to_market",
    ],
}

INPUTS = [
    "total_assets",
    "total_liabilities",
    "cash",
    "operating_cash_flow",
    "shares_outstanding",
    "close",
    "annual_net_income",
    "annual_revenue",
    "annual_operating_cash_flow",
    "annual_capital_expenditures",
    "shareholders_equity",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit current long_growth_v1 family coverage and missing-data causes."
    )
    parser.add_argument("--composite", type=Path, required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/current_family_coverage_audit"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.composite, low_memory=False)
    current = frame.loc[frame["decision_date"].astype(str).eq(args.decision_date)].copy()
    if current.empty:
        raise ValueError(f"No rows found for decision date {args.decision_date}")

    family_rows = []
    factor_rows = []
    reason_rows = []

    for family, factors in FAMILIES.items():
        family_rows.append(
            {
                "family": family,
                "rows": len(current),
                "eligible": int(current[f"{family}_eligible"].fillna(False).sum()),
                "coverage_pct": float(current[f"{family}_score"].notna().mean()),
                "factor_count_0": int(current[f"{family}_factor_count"].eq(0).sum()),
                "factor_count_1": int(current[f"{family}_factor_count"].eq(1).sum()),
                "factor_count_2": int(current[f"{family}_factor_count"].eq(2).sum()),
                "factor_count_3": int(current[f"{family}_factor_count"].eq(3).sum()),
                "factor_count_4": int(current[f"{family}_factor_count"].eq(4).sum())
                if f"{family}_factor_count" in current.columns
                else 0,
            }
        )

        for factor in factors:
            raw = factor if factor in current.columns else None
            validated = f"{factor}_validated"
            invalid_reason = f"{factor}_invalid_reason"
            factor_rows.append(
                {
                    "family": family,
                    "factor": factor,
                    "raw_available": int(current[raw].notna().sum()) if raw else 0,
                    "validated_available": int(current[validated].notna().sum())
                    if validated in current.columns
                    else 0,
                    "rejected_after_raw": int(
                        (
                            current[raw].notna()
                            & current[validated].isna()
                        ).sum()
                    )
                    if raw and validated in current.columns
                    else 0,
                }
            )

            if raw and validated in current.columns and invalid_reason in current.columns:
                rejected = current.loc[
                    current[raw].notna() & current[validated].isna(),
                    invalid_reason,
                ].fillna("")
                for reason, count in rejected.value_counts().items():
                    reason_rows.append(
                        {
                            "family": family,
                            "factor": factor,
                            "invalid_reason": reason or "(blank)",
                            "rows": int(count),
                        }
                    )

    input_rows = []
    for column in INPUTS:
        if column in current.columns:
            input_rows.append(
                {
                    "input": column,
                    "available": int(current[column].notna().sum()),
                    "missing": int(current[column].isna().sum()),
                    "coverage_pct": float(current[column].notna().mean()),
                }
            )

    summary = pd.DataFrame(
        [
            {
                "decision_date": args.decision_date,
                "rows": len(current),
                "research_ready": int(current["research_ready"].fillna(False).sum()),
                "composite_scored": int(current["long_growth_v1_score"].notna().sum()),
                "full_family_coverage": int(current["full_family_coverage"].fillna(False).sum()),
                "top_conviction_eligible": int(
                    current["top_conviction_eligible"].fillna(False).sum()
                ),
            }
        ]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    pd.DataFrame(family_rows).to_csv(
        args.output_dir / "family_coverage.csv", index=False
    )
    pd.DataFrame(factor_rows).to_csv(
        args.output_dir / "factor_coverage.csv", index=False
    )
    pd.DataFrame(input_rows).to_csv(
        args.output_dir / "input_coverage.csv", index=False
    )
    pd.DataFrame(reason_rows).to_csv(
        args.output_dir / "validation_rejections.csv", index=False
    )

    print("CURRENT FAMILY COVERAGE AUDIT")
    print(f"Decision date:             {args.decision_date}")
    print(f"Rows:                      {len(current):,}")
    print(f"Research ready:            {int(current['research_ready'].fillna(False).sum()):,}")
    print(f"Composite scored:          {int(current['long_growth_v1_score'].notna().sum()):,}")
    print(f"Full four-family coverage: {int(current['full_family_coverage'].fillna(False).sum()):,}")
    print(f"Top conviction eligible:   {int(current['top_conviction_eligible'].fillna(False).sum()):,}")
    print()
    for row in family_rows:
        print(
            f"{row['family']:20s} eligible={row['eligible']:4d}/{row['rows']} "
            f"coverage={row['coverage_pct']:.2%} "
            f"counts(0/1/2/3/4)="
            f"{row['factor_count_0']}/"
            f"{row['factor_count_1']}/"
            f"{row['factor_count_2']}/"
            f"{row['factor_count_3']}/"
            f"{row['factor_count_4']}"
        )
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
