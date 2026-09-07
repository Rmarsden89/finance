from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.models import FULL_GROWTH_V1, add_full_growth_v1_scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the versioned full_growth_v1 challenger composite."
    )
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/full_growth_v1.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.family_scores, low_memory=False)
    scored = add_full_growth_v1_scores(frame)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.output, index=False)

    print("FULL GROWTH V1 BUILD")
    print(f"Model: {FULL_GROWTH_V1.model_id}")
    print(f"Rows: {len(scored):,}")
    print(f"Composite coverage: {scored['full_growth_v1_score'].notna().mean():.2%}")
    print(f"Top conviction ready: {scored['top_conviction_eligible'].mean():.2%}")
    print(f"Full six-family coverage: {scored['full_family_coverage'].mean():.2%}")
    print(f"Evaluation eligible: {scored['evaluation_eligible'].mean():.2%}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
