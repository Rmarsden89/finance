from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FullGrowthModelDefinition:
    model_id: str
    family_weights: dict[str, float]
    required_families: tuple[str, ...]
    supporting_families: tuple[str, ...]
    minimum_supporting_families: int
    top_conviction_required_families: tuple[str, ...]
    evaluation_start: date


FULL_GROWTH_V1 = FullGrowthModelDefinition(
    model_id="full_growth_v1",
    family_weights={
        "quality": 0.30,
        "financial_health": 0.20,
        "growth": 0.20,
        "valuation": 0.15,
        "stability": 0.10,
        "momentum": 0.05,
    },
    required_families=("quality", "growth"),
    supporting_families=(
        "financial_health",
        "valuation",
        "stability",
    ),
    minimum_supporting_families=2,
    top_conviction_required_families=(
        "quality",
        "financial_health",
        "growth",
        "valuation",
        "stability",
    ),
    evaluation_start=date(2016, 1, 1),
)


def add_full_growth_v1_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add immutable full-growth V1 challenger scores and governance flags.

    Momentum may contribute to an otherwise eligible score but is deliberately
    excluded from the eligibility gate and top-conviction requirement.
    """

    result = frame.copy()
    family_values: dict[str, pd.Series] = {}

    weighted_sum = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in FULL_GROWTH_V1.family_weights.items():
        column = f"{family}_score"
        if column not in result.columns:
            raise ValueError(f"Missing required family score column: {column}")

        values = pd.to_numeric(result[column], errors="coerce")
        family_values[family] = values
        available = values.notna()

        weighted_sum.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        available_count.loc[available] += 1

        result[f"{family}_missing"] = ~available

    required_present = pd.Series(True, index=result.index, dtype="bool")
    for family in FULL_GROWTH_V1.required_families:
        required_present &= family_values[family].notna()

    supporting_count = pd.Series(0, index=result.index, dtype="int64")
    for family in FULL_GROWTH_V1.supporting_families:
        supporting_count += family_values[family].notna().astype("int64")

    eligible = (
        required_present
        & (
            supporting_count
            >= FULL_GROWTH_V1.minimum_supporting_families
        )
        & (available_weight > 0)
    )

    composite = pd.Series(np.nan, index=result.index, dtype="float64")
    composite.loc[eligible] = (
        weighted_sum.loc[eligible]
        / available_weight.loc[eligible]
    )

    top_required_present = pd.Series(
        True,
        index=result.index,
        dtype="bool",
    )
    for family in FULL_GROWTH_V1.top_conviction_required_families:
        top_required_present &= family_values[family].notna()

    result["full_growth_v1_family_count"] = available_count
    result["full_growth_v1_supporting_family_count"] = supporting_count
    result["full_growth_v1_weight_coverage"] = available_weight
    result["full_growth_v1_eligible"] = eligible
    result["full_growth_v1_score"] = composite
    result["full_family_coverage"] = available_count.eq(
        len(FULL_GROWTH_V1.family_weights)
    )
    result["top_conviction_eligible"] = (
        composite.notna()
        & top_required_present
    )

    decision_date = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )
    evaluation_start = pd.Timestamp(FULL_GROWTH_V1.evaluation_start)
    result["evaluation_eligible"] = (
        composite.notna()
        & decision_date.notna()
        & (decision_date >= evaluation_start)
    )

    result["model_id"] = FULL_GROWTH_V1.model_id
    return result
