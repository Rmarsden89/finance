from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.factors.registry import FACTOR_REGISTRY
from finance.scoring import (
    DEFAULT_NORMALIZATION,
    NormalizationConfig,
    normalize_validated_factors,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize validated V1 raw factors within each weekly cross-section."
    )
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--lower-quantile",
        type=float,
        default=DEFAULT_NORMALIZATION.lower_quantile,
    )
    parser.add_argument(
        "--upper-quantile",
        type=float,
        default=DEFAULT_NORMALIZATION.upper_quantile,
    )
    parser.add_argument(
        "--min-cross-section",
        type=int,
        default=DEFAULT_NORMALIZATION.min_cross_section,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/normalized_factors_v1.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.factors, low_memory=False)

    config = NormalizationConfig(
        lower_quantile=args.lower_quantile,
        upper_quantile=args.upper_quantile,
        min_cross_section=args.min_cross_section,
    )
    normalized = normalize_validated_factors(frame, config=config)

    keep = list(frame.columns)
    for factor in FACTOR_REGISTRY:
        for suffix in (
            "_winsorized",
            "_winsorized_flag",
            "_percentile",
            "_score",
        ):
            column = f"{factor}{suffix}"
            if column in normalized.columns:
                keep.append(column)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    normalized[keep].to_csv(args.output, index=False)

    print("NORMALIZED FACTOR BUILD V1")
    print(f"Rows:               {len(normalized):,}")
    print(f"Lower quantile:     {config.lower_quantile:.3f}")
    print(f"Upper quantile:     {config.upper_quantile:.3f}")
    print(f"Min cross-section:  {config.min_cross_section}")
    print(f"Output:             {args.output}")
    print()
    print("NORMALIZED COVERAGE")
    for factor in FACTOR_REGISTRY:
        score_col = f"{factor}_score"
        if score_col not in normalized.columns:
            continue
        available = int(normalized[score_col].notna().sum())
        share = available / len(normalized) if len(normalized) else 0.0
        clipped = int(normalized[f"{factor}_winsorized_flag"].sum())
        print(
            f"{factor:38s} "
            f"score={share:7.2%} "
            f"winsorized={clipped:7,d}"
        )


if __name__ == "__main__":
    main()
