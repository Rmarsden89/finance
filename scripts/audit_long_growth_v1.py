from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit long_growth_v1 coverage, family influence, and rankings."
    )
    parser.add_argument("--composite", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/long_growth_v1_validation"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.composite, low_memory=False)
    frame["decision_date"] = pd.to_datetime(frame["decision_date"], errors="coerce")
    frame["year"] = frame["decision_date"].dt.year

    score = pd.to_numeric(frame["long_growth_v1_score"], errors="coerce")

    summary = pd.DataFrame([{
        "rows": len(frame),
        "scored": int(score.notna().sum()),
        "coverage_pct": score.notna().mean(),
        "full_family_coverage_pct": frame["full_family_coverage"].mean(),
        "top_conviction_eligible_pct": frame["top_conviction_eligible"].mean(),
        "evaluation_eligible_pct": frame["evaluation_eligible"].mean(),
        "health_missing_pct": frame["health_missing"].mean(),
        "growth_missing_pct": frame["growth_missing"].mean(),
        "valuation_missing_pct": frame["valuation_missing"].mean(),
        "score_min": score.min(),
        "score_p05": score.quantile(0.05),
        "score_median": score.median(),
        "score_p95": score.quantile(0.95),
        "score_max": score.max(),
        "out_of_bounds": int(((score.dropna() < 0) | (score.dropna() > 100)).sum()),
    }])

    yearly_rows = []
    for year, group in frame.groupby("year", dropna=True):
        ys = pd.to_numeric(group["long_growth_v1_score"], errors="coerce")
        yearly_rows.append({
            "year": int(year),
            "rows": len(group),
            "scored": int(ys.notna().sum()),
            "coverage_pct": ys.notna().mean(),
            "full_family_coverage_pct": group["full_family_coverage"].mean(),
            "top_conviction_eligible_pct": group["top_conviction_eligible"].mean(),
            "evaluation_eligible_pct": group["evaluation_eligible"].mean(),
            "health_missing_pct": group["health_missing"].mean(),
            "growth_missing_pct": group["growth_missing"].mean(),
            "valuation_missing_pct": group["valuation_missing"].mean(),
            "median": ys.median(),
            "p05": ys.quantile(0.05),
            "p95": ys.quantile(0.95),
        })
    yearly = pd.DataFrame(yearly_rows)

    family_columns = [
        "quality_score",
        "financial_health_score",
        "growth_score",
        "valuation_score",
        "long_growth_v1_score",
    ]
    correlations = frame[family_columns].corr(method="spearman")

    top_rows = []
    for decision_date, group in frame.groupby("decision_date", sort=False):
        eligible = group.loc[
            group["top_conviction_eligible"].fillna(False)
            & group["long_growth_v1_score"].notna()
        ].sort_values("long_growth_v1_score", ascending=False).head(10)

        for rank, row in enumerate(eligible.itertuples(index=False), start=1):
            top_rows.append({
                "decision_date": decision_date,
                "rank": rank,
                "ticker": getattr(row, "ticker", ""),
                "company_name": getattr(row, "company_name", ""),
                "long_growth_v1_score": getattr(row, "long_growth_v1_score"),
                "quality_score": getattr(row, "quality_score"),
                "financial_health_score": getattr(row, "financial_health_score"),
                "growth_score": getattr(row, "growth_score"),
                "valuation_score": getattr(row, "valuation_score"),
                "family_count": getattr(row, "long_growth_v1_family_count"),
            })
    top = pd.DataFrame(top_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "long_growth_summary.csv", index=False)
    yearly.to_csv(args.output_dir / "long_growth_yearly.csv", index=False)
    correlations.to_csv(args.output_dir / "long_growth_family_spearman.csv")
    top.to_csv(args.output_dir / "long_growth_top10_weekly.csv", index=False)

    print("LONG GROWTH V1 AUDIT")
    print(f"Rows: {len(frame):,}")
    print(f"Coverage: {score.notna().mean():.2%}")
    print(f"Full family coverage: {frame['full_family_coverage'].mean():.2%}")
    print(f"Top conviction ready: {frame['top_conviction_eligible'].mean():.2%}")
    print(f"Evaluation eligible: {frame['evaluation_eligible'].mean():.2%}")
    print(f"Out-of-bounds scores: {int(summary.loc[0, 'out_of_bounds']):,}")
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
