from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.models import CORE_BUSINESS_V1, add_core_business_v1_scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the versioned core_business_v1 composite."
    )
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/core_business_v1.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.family_scores, low_memory=False)
    scored = add_core_business_v1_scores(frame)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.output, index=False)

    print("CORE BUSINESS V1 BUILD")
    print(f"Model: {CORE_BUSINESS_V1.model_id}")
    print(f"Rows: {len(scored):,}")
    print(f"Composite coverage: {scored['core_business_v1_score'].notna().mean():.2%}")
    print(f"Health missing: {scored['health_missing'].mean():.2%}")
    print(f"Top conviction ready: {scored['top_conviction_eligible'].mean():.2%}")
    print(f"Evaluation eligible: {scored['evaluation_eligible'].mean():.2%}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
