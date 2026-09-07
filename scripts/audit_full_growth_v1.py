from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


FAMILY_COLUMNS = [
    "quality_score",
    "financial_health_score",
    "growth_score",
    "valuation_score",
    "stability_score",
    "momentum_score",
    "full_growth_v1_score",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit full_growth_v1 coverage, family influence, rankings, and "
            "side-by-side behavior versus long_growth_v1."
        )
    )
    parser.add_argument("--composite", type=Path, required=True)
    parser.add_argument("--long-growth", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/full_growth_v1_validation"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.composite, low_memory=False)
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"],
        errors="coerce",
    )
    frame["year"] = frame["decision_date"].dt.year

    score = pd.to_numeric(frame["full_growth_v1_score"], errors="coerce")

    summary = pd.DataFrame([{
        "rows": len(frame),
        "scored": int(score.notna().sum()),
        "coverage_pct": score.notna().mean(),
        "top_conviction_eligible_pct": frame["top_conviction_eligible"].mean(),
        "full_six_family_coverage_pct": frame["full_family_coverage"].mean(),
        "evaluation_eligible_pct": frame["evaluation_eligible"].mean(),
        "quality_missing_pct": frame["quality_missing"].mean(),
        "financial_health_missing_pct": frame["financial_health_missing"].mean(),
        "growth_missing_pct": frame["growth_missing"].mean(),
        "valuation_missing_pct": frame["valuation_missing"].mean(),
        "stability_missing_pct": frame["stability_missing"].mean(),
        "momentum_missing_pct": frame["momentum_missing"].mean(),
        "score_min": score.min(),
        "score_p05": score.quantile(0.05),
        "score_median": score.median(),
        "score_p95": score.quantile(0.95),
        "score_max": score.max(),
        "out_of_bounds": int(
            ((score.dropna() < 0) | (score.dropna() > 100)).sum()
        ),
    }])

    yearly_rows = []
    for year, group in frame.groupby("year", dropna=True):
        ys = pd.to_numeric(group["full_growth_v1_score"], errors="coerce")
        yearly_rows.append({
            "year": int(year),
            "rows": len(group),
            "scored": int(ys.notna().sum()),
            "coverage_pct": ys.notna().mean(),
            "top_conviction_eligible_pct": group[
                "top_conviction_eligible"
            ].mean(),
            "full_six_family_coverage_pct": group[
                "full_family_coverage"
            ].mean(),
            "evaluation_eligible_pct": group["evaluation_eligible"].mean(),
            "median": ys.median(),
            "p05": ys.quantile(0.05),
            "p95": ys.quantile(0.95),
        })
    yearly = pd.DataFrame(yearly_rows)

    available_family_columns = [
        column
        for column in FAMILY_COLUMNS
        if column in frame.columns
    ]
    correlations = frame[available_family_columns].corr(method="spearman")

    eligibility_patterns = (
        frame.groupby(
            [
                "quality_missing",
                "financial_health_missing",
                "growth_missing",
                "valuation_missing",
                "stability_missing",
                "momentum_missing",
                "full_growth_v1_eligible",
            ],
            dropna=False,
        )
        .size()
        .reset_index(name="rows")
        .sort_values("rows", ascending=False)
    )

    top_rows = []
    for decision_date, group in frame.groupby(
        "decision_date",
        sort=False,
    ):
        eligible = group.loc[
            group["top_conviction_eligible"].fillna(False)
            & group["full_growth_v1_score"].notna()
        ].sort_values(
            "full_growth_v1_score",
            ascending=False,
        ).head(10)

        for rank, row in enumerate(
            eligible.itertuples(index=False),
            start=1,
        ):
            top_rows.append({
                "decision_date": decision_date,
                "rank": rank,
                "ticker": getattr(row, "ticker", ""),
                "company_name": getattr(row, "company_name", ""),
                "full_growth_v1_score": getattr(
                    row,
                    "full_growth_v1_score",
                ),
                "quality_score": getattr(row, "quality_score"),
                "financial_health_score": getattr(
                    row,
                    "financial_health_score",
                ),
                "growth_score": getattr(row, "growth_score"),
                "valuation_score": getattr(row, "valuation_score"),
                "stability_score": getattr(row, "stability_score"),
                "momentum_score": getattr(row, "momentum_score"),
                "family_count": getattr(
                    row,
                    "full_growth_v1_family_count",
                ),
                "weight_coverage": getattr(
                    row,
                    "full_growth_v1_weight_coverage",
                ),
            })
    top = pd.DataFrame(top_rows)

    comparison_summary = pd.DataFrame()
    comparison_weekly = pd.DataFrame()

    if args.long_growth is not None:
        old = pd.read_csv(args.long_growth, low_memory=False)
        old["decision_date"] = pd.to_datetime(
            old["decision_date"],
            errors="coerce",
        )

        keys = ["decision_date", "ticker"]
        old_cols = [
            column
            for column in (
                "decision_date",
                "ticker",
                "long_growth_v1_score",
                "top_conviction_eligible",
            )
            if column in old.columns
        ]
        merged = frame.merge(
            old[old_cols],
            on=keys,
            how="left",
            suffixes=("", "_long_growth"),
        )

        both = merged[
            merged["full_growth_v1_score"].notna()
            & merged["long_growth_v1_score"].notna()
        ].copy()

        both["score_delta"] = (
            both["full_growth_v1_score"]
            - both["long_growth_v1_score"]
        )

        comparison_summary = pd.DataFrame([{
            "rows_both_scored": len(both),
            "spearman_score_correlation": both[
                ["full_growth_v1_score", "long_growth_v1_score"]
            ].corr(method="spearman").iloc[0, 1] if len(both) else float("nan"),
            "mean_score_delta": both["score_delta"].mean(),
            "median_score_delta": both["score_delta"].median(),
            "p05_score_delta": both["score_delta"].quantile(0.05),
            "p95_score_delta": both["score_delta"].quantile(0.95),
            "full_growth_coverage_pct": frame[
                "full_growth_v1_score"
            ].notna().mean(),
            "long_growth_coverage_pct": old[
                "long_growth_v1_score"
            ].notna().mean(),
        }])

        weekly_rows = []
        for decision_date, group in merged.groupby(
            "decision_date",
            sort=False,
        ):
            full_top = set(
                group.loc[
                    group["full_growth_v1_score"].notna()
                    & group["top_conviction_eligible"].fillna(False),
                    ["ticker", "full_growth_v1_score"],
                ]
                .sort_values("full_growth_v1_score", ascending=False)
                .head(10)["ticker"]
            )

            old_top_flag = "top_conviction_eligible_long_growth"
            if old_top_flag in group.columns:
                old_mask = group[old_top_flag].fillna(False)
            else:
                old_mask = pd.Series(True, index=group.index)

            long_top = set(
                group.loc[
                    group["long_growth_v1_score"].notna()
                    & old_mask,
                    ["ticker", "long_growth_v1_score"],
                ]
                .sort_values("long_growth_v1_score", ascending=False)
                .head(10)["ticker"]
            )

            union = full_top | long_top
            overlap = full_top & long_top
            weekly_rows.append({
                "decision_date": decision_date,
                "full_top10_count": len(full_top),
                "long_top10_count": len(long_top),
                "top10_overlap_count": len(overlap),
                "top10_jaccard": (
                    len(overlap) / len(union)
                    if union
                    else float("nan")
                ),
            })
        comparison_weekly = pd.DataFrame(weekly_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(
        args.output_dir / "full_growth_summary.csv",
        index=False,
    )
    yearly.to_csv(
        args.output_dir / "full_growth_yearly.csv",
        index=False,
    )
    correlations.to_csv(
        args.output_dir / "full_growth_family_spearman.csv",
    )
    eligibility_patterns.to_csv(
        args.output_dir / "full_growth_eligibility_patterns.csv",
        index=False,
    )
    top.to_csv(
        args.output_dir / "full_growth_top10_weekly.csv",
        index=False,
    )

    if not comparison_summary.empty:
        comparison_summary.to_csv(
            args.output_dir / "full_vs_long_summary.csv",
            index=False,
        )
        comparison_weekly.to_csv(
            args.output_dir / "full_vs_long_weekly_top10.csv",
            index=False,
        )

    print("FULL GROWTH V1 AUDIT")
    print(f"Rows: {len(frame):,}")
    print(f"Coverage: {score.notna().mean():.2%}")
    print(
        "Top conviction ready: "
        f"{frame['top_conviction_eligible'].mean():.2%}"
    )
    print(
        "Full six-family coverage: "
        f"{frame['full_family_coverage'].mean():.2%}"
    )
    print(
        "Evaluation eligible: "
        f"{frame['evaluation_eligible'].mean():.2%}"
    )
    print(
        "Out-of-bounds scores: "
        f"{int(summary.loc[0, 'out_of_bounds']):,}"
    )
    if not comparison_summary.empty:
        print(
            "Score correlation vs long_growth_v1: "
            f"{comparison_summary.loc[0, 'spearman_score_correlation']:.3f}"
        )
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
