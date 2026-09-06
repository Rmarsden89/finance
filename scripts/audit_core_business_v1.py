from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit core_business_v1 coverage and weekly rankings."
    )
    parser.add_argument("--composite", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/core_business_v1_validation"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.composite, low_memory=False)
    frame["decision_date"] = pd.to_datetime(frame["decision_date"], errors="coerce")
    frame["year"] = frame["decision_date"].dt.year
    score = pd.to_numeric(frame["core_business_v1_score"], errors="coerce")

    summary = pd.DataFrame([{
        "rows": len(frame),
        "scored": int(score.notna().sum()),
        "coverage_pct": score.notna().mean(),
        "health_missing_pct": frame["health_missing"].mean(),
        "top_conviction_eligible_pct": frame["top_conviction_eligible"].mean(),
        "evaluation_eligible_pct": frame["evaluation_eligible"].mean(),
        "score_min": score.min(),
        "score_p05": score.quantile(0.05),
        "score_median": score.median(),
        "score_p95": score.quantile(0.95),
        "score_max": score.max(),
        "out_of_bounds": int(((score.dropna() < 0) | (score.dropna() > 100)).sum()),
    }])

    yearly_rows = []
    for year, group in frame.groupby("year", dropna=True):
        ys = pd.to_numeric(group["core_business_v1_score"], errors="coerce")
        yearly_rows.append({
            "year": int(year),
            "rows": len(group),
            "scored": int(ys.notna().sum()),
            "coverage_pct": ys.notna().mean(),
            "health_missing_pct": group["health_missing"].mean(),
            "top_conviction_eligible_pct": group["top_conviction_eligible"].mean(),
            "evaluation_eligible_pct": group["evaluation_eligible"].mean(),
            "median": ys.median(),
            "p05": ys.quantile(0.05),
            "p95": ys.quantile(0.95),
        })
    yearly = pd.DataFrame(yearly_rows)

    family_columns = [
        "quality_score",
        "financial_health_score",
        "growth_score",
        "core_business_v1_score",
    ]
    correlations = frame[family_columns].corr(method="spearman")

    top_rows = []
    for decision_date, group in frame.groupby("decision_date", sort=False):
        eligible = group.loc[
            group["top_conviction_eligible"].fillna(False)
            & group["core_business_v1_score"].notna()
        ].sort_values("core_business_v1_score", ascending=False).head(10)

        for rank, row in enumerate(eligible.itertuples(index=False), start=1):
            top_rows.append({
                "decision_date": decision_date,
                "rank": rank,
                "ticker": getattr(row, "ticker", ""),
                "company_name": getattr(row, "company_name", ""),
                "core_business_v1_score": getattr(row, "core_business_v1_score"),
                "quality_score": getattr(row, "quality_score"),
                "financial_health_score": getattr(row, "financial_health_score"),
                "growth_score": getattr(row, "growth_score"),
                "family_count": getattr(row, "core_business_v1_family_count"),
            })
    top = pd.DataFrame(top_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "composite_summary.csv", index=False)
    yearly.to_csv(args.output_dir / "composite_yearly.csv", index=False)
    correlations.to_csv(args.output_dir / "composite_family_spearman.csv")
    top.to_csv(args.output_dir / "composite_top10_weekly.csv", index=False)

    print("CORE BUSINESS V1 AUDIT")
    print(f"Rows: {len(frame):,}")
    print(f"Coverage: {score.notna().mean():.2%}")
    print(f"Health missing: {frame['health_missing'].mean():.2%}")
    print(f"Top conviction ready: {frame['top_conviction_eligible'].mean():.2%}")
    print(f"Evaluation eligible: {frame['evaluation_eligible'].mean():.2%}")
    print(f"Out-of-bounds scores: {int(summary.loc[0, 'out_of_bounds']):,}")
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
