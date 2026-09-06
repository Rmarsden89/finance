from __future__ import annotations

import pandas as pd

from finance.scoring.normalize import (
    NormalizationConfig,
    normalize_validated_factors,
)


def test_normalization_winsorizes_weekly_and_respects_direction() -> None:
    rows = []
    for i in range(100):
        rows.append(
            {
                "decision_date": "2025-01-03",
                "return_on_assets_validated": float(i),
                "liabilities_to_assets_validated": float(i),
            }
        )
    frame = pd.DataFrame(rows)

    result = normalize_validated_factors(
        frame,
        config=NormalizationConfig(
            lower_quantile=0.01,
            upper_quantile=0.99,
            min_cross_section=20,
        ),
    )

    assert result["return_on_assets_score"].notna().all()
    assert result["liabilities_to_assets_score"].notna().all()

    # Higher ROA should score better.
    assert (
        result.loc[99, "return_on_assets_score"]
        > result.loc[0, "return_on_assets_score"]
    )

    # Lower liabilities/assets should score better.
    assert (
        result.loc[0, "liabilities_to_assets_score"]
        > result.loc[99, "liabilities_to_assets_score"]
    )

    assert result["return_on_assets_score"].between(0, 100).all()
    assert result["liabilities_to_assets_score"].between(0, 100).all()

    assert result["return_on_assets_winsorized_flag"].sum() == 2
    assert result["liabilities_to_assets_winsorized_flag"].sum() == 2


def test_normalization_requires_minimum_cross_section() -> None:
    frame = pd.DataFrame(
        {
            "decision_date": ["2025-01-03"] * 5,
            "return_on_assets_validated": [1, 2, 3, 4, 5],
        }
    )

    result = normalize_validated_factors(
        frame,
        config=NormalizationConfig(min_cross_section=20),
    )

    assert result["return_on_assets_score"].isna().all()
