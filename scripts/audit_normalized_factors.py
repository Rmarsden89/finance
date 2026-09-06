from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from finance.factors.registry import FACTOR_REGISTRY


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit normalized V1 factor scores."
    )
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/normalization_validation_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.normalized, low_memory=False)
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"],
        errors="coerce",
    )
    frame["year"] = frame["decision_date"].dt.year

    summary_rows = []
    yearly_rows = []

    score_columns = []
    for factor, definition in FACTOR_REGISTRY.items():
        score_col = f"{factor}_score"
        winsor_flag = f"{factor}_winsorized_flag"
        if score_col not in frame.columns:
            continue

        score_columns.append(score_col)
        scores = pd.to_numeric(frame[score_col], errors="coerce")
        finite = scores[np.isfinite(scores)]
        raw_available = int(
            pd.to_numeric(
                frame.get(f"{factor}_validated"),
                errors="coerce",
            ).notna().sum()
        )
        scored = int(scores.notna().sum())
        winsorized = int(
            frame.get(winsor_flag, pd.Series(False, index=frame.index))
            .fillna(False)
            .astype(bool)
            .sum()
        )

        summary_rows.append(
            {
                "factor": factor,
                "family": definition.family,
                "direction": definition.direction,
                "rows": len(frame),
                "validated_available": raw_available,
                "scored": scored,
                "score_coverage_pct": (
                    scored / len(frame) if len(frame) else 0.0
                ),
                "winsorized_rows": winsorized,
                "winsorized_pct_of_scored": (
                    winsorized / scored if scored else 0.0
                ),
                "score_min": finite.min() if not finite.empty else np.nan,
                "score_p05": finite.quantile(0.05) if not finite.empty else np.nan,
                "score_median": finite.median() if not finite.empty else np.nan,
                "score_p95": finite.quantile(0.95) if not finite.empty else np.nan,
                "score_max": finite.max() if not finite.empty else np.nan,
            }
        )

        for year, group in frame.groupby("year", dropna=True):
            year_scores = pd.to_numeric(group[score_col], errors="coerce")
            year_finite = year_scores[np.isfinite(year_scores)]
            yearly_rows.append(
                {
                    "year": int(year),
                    "factor": factor,
                    "family": definition.family,
                    "rows": len(group),
                    "scored": int(year_scores.notna().sum()),
                    "score_coverage_pct": (
                        year_scores.notna().mean()
                        if len(group)
                        else 0.0
                    ),
                    "median": (
                        year_finite.median()
                        if not year_finite.empty
                        else np.nan
                    ),
                    "p05": (
                        year_finite.quantile(0.05)
                        if not year_finite.empty
                        else np.nan
                    ),
                    "p95": (
                        year_finite.quantile(0.95)
                        if not year_finite.empty
                        else np.nan
                    ),
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(summary_rows)
    yearly = pd.DataFrame(yearly_rows)

    summary.to_csv(
        args.output_dir / "normalization_summary.csv",
        index=False,
    )
    yearly.to_csv(
        args.output_dir / "normalization_yearly.csv",
        index=False,
    )

    correlations = pd.DataFrame()
    if score_columns:
        correlations = frame[score_columns].corr(method="spearman")
        correlations.to_csv(
            args.output_dir / "factor_score_spearman.csv"
        )

    weekly_rank_checks = []
    for factor in FACTOR_REGISTRY:
        score_col = f"{factor}_score"
        if score_col not in frame.columns:
            continue
        for decision_date, group in frame.groupby("decision_date", sort=False):
            scores = pd.to_numeric(group[score_col], errors="coerce").dropna()
            if scores.empty:
                continue
            weekly_rank_checks.append(
                {
                    "decision_date": decision_date,
                    "factor": factor,
                    "scored": len(scores),
                    "min_score": scores.min(),
                    "median_score": scores.median(),
                    "max_score": scores.max(),
                    "out_of_bounds": int(((scores < 0) | (scores > 100)).sum()),
                }
            )

    weekly_checks = pd.DataFrame(weekly_rank_checks)
    weekly_checks.to_csv(
        args.output_dir / "normalization_weekly_checks.csv",
        index=False,
    )

    print("NORMALIZATION AUDIT V1")
    print(f"Rows:       {len(frame):,}")
    print(f"Factors:    {len(summary):,}")
    print()
    for row in summary.itertuples(index=False):
        print(
            f"{row.factor:38s} "
            f"coverage={row.score_coverage_pct:7.2%} "
            f"winsorized={row.winsorized_rows:7,d} "
            f"median={row.score_median:7.2f} "
            f"p05={row.score_p05:7.2f} "
            f"p95={row.score_p95:7.2f}"
        )

    out_of_bounds = (
        int(weekly_checks["out_of_bounds"].sum())
        if not weekly_checks.empty
        else 0
    )
    print()
    print(f"Out-of-bounds scores: {out_of_bounds:,}")
    print(f"Reports:              {args.output_dir}")


if __name__ == "__main__":
    main()
