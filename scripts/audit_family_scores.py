from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from finance.scoring.family_scores import FAMILY_DEFINITIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit V1 family scores and component availability."
    )
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/family_validation_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.family_scores, low_memory=False)
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"],
        errors="coerce",
    )
    frame["year"] = frame["decision_date"].dt.year

    summary_rows = []
    yearly_rows = []
    availability_rows = []

    for family, definition in FAMILY_DEFINITIONS.items():
        score_col = f"{family}_score"
        count_col = f"{family}_factor_count"
        weight_col = f"{family}_weight_coverage"

        scores = pd.to_numeric(frame[score_col], errors="coerce")
        finite = scores[np.isfinite(scores)]

        summary_rows.append(
            {
                "family": family,
                "rows": len(frame),
                "scored": int(scores.notna().sum()),
                "coverage_pct": (
                    scores.notna().mean() if len(frame) else 0.0
                ),
                "min": finite.min() if not finite.empty else np.nan,
                "p05": finite.quantile(0.05) if not finite.empty else np.nan,
                "median": finite.median() if not finite.empty else np.nan,
                "p95": finite.quantile(0.95) if not finite.empty else np.nan,
                "max": finite.max() if not finite.empty else np.nan,
                "minimum_factors": definition.minimum_factors,
            }
        )

        counts = pd.to_numeric(frame[count_col], errors="coerce")
        weights = pd.to_numeric(frame[weight_col], errors="coerce")
        for count, group in frame.assign(
            _count=counts,
            _weight=weights,
        ).groupby("_count", dropna=False):
            availability_rows.append(
                {
                    "family": family,
                    "factor_count": count,
                    "rows": len(group),
                    "pct": len(group) / len(frame) if len(frame) else 0.0,
                    "median_weight_coverage": (
                        pd.to_numeric(
                            group["_weight"],
                            errors="coerce",
                        ).median()
                    ),
                }
            )

        for year, group in frame.groupby("year", dropna=True):
            ys = pd.to_numeric(group[score_col], errors="coerce")
            yf = ys[np.isfinite(ys)]
            yearly_rows.append(
                {
                    "year": int(year),
                    "family": family,
                    "rows": len(group),
                    "scored": int(ys.notna().sum()),
                    "coverage_pct": (
                        ys.notna().mean() if len(group) else 0.0
                    ),
                    "median": yf.median() if not yf.empty else np.nan,
                    "p05": yf.quantile(0.05) if not yf.empty else np.nan,
                    "p95": yf.quantile(0.95) if not yf.empty else np.nan,
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary = pd.DataFrame(summary_rows)
    yearly = pd.DataFrame(yearly_rows)
    availability = pd.DataFrame(availability_rows)

    summary.to_csv(
        args.output_dir / "family_summary.csv",
        index=False,
    )
    yearly.to_csv(
        args.output_dir / "family_yearly.csv",
        index=False,
    )
    availability.to_csv(
        args.output_dir / "family_factor_availability.csv",
        index=False,
    )

    family_score_columns = [
        f"{family}_score" for family in FAMILY_DEFINITIONS
    ]
    frame[family_score_columns].corr(
        method="spearman"
    ).to_csv(
        args.output_dir / "family_score_spearman.csv"
    )

    print("FAMILY SCORE AUDIT V1")
    print(f"Rows: {len(frame):,}")
    print()
    for row in summary.itertuples(index=False):
        print(
            f"{row.family:20s} "
            f"coverage={row.coverage_pct:7.2%} "
            f"median={row.median:7.2f} "
            f"p05={row.p05:7.2f} "
            f"p95={row.p95:7.2f}"
        )
    print()
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
