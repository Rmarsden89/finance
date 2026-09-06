from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.scoring.family_scores import (
    FAMILY_DEFINITIONS,
    add_family_scores,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build V1 family scores from normalized factor scores."
    )
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/family_scores_v1.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.normalized, low_memory=False)
    scored = add_family_scores(frame)

    keep = list(frame.columns)
    for family in FAMILY_DEFINITIONS:
        for suffix in (
            "_factor_count",
            "_weight_coverage",
            "_eligible",
            "_score",
        ):
            column = f"{family}{suffix}"
            if column in scored.columns:
                keep.append(column)

    if "positive_operating_cash_flow_flag" in scored.columns:
        keep.append("positive_operating_cash_flow_flag")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    scored[keep].to_csv(args.output, index=False)

    print("FAMILY SCORE BUILD V1")
    print(f"Rows:    {len(scored):,}")
    print(f"Output:  {args.output}")
    print()
    for family in FAMILY_DEFINITIONS:
        score_col = f"{family}_score"
        eligible_col = f"{family}_eligible"
        available = int(scored[score_col].notna().sum())
        eligible = int(scored[eligible_col].sum())
        share = available / len(scored) if len(scored) else 0.0
        print(
            f"{family:20s} "
            f"eligible={eligible:9,d} "
            f"coverage={share:7.2%}"
        )


if __name__ == "__main__":
    main()
