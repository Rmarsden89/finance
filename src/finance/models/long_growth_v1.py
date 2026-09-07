from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LongGrowthModelDefinition:
    model_id: str
    family_weights: dict[str, float]
    minimum_families: int
    evaluation_start: date


LONG_GROWTH_V1 = LongGrowthModelDefinition(
    model_id="long_growth_v1",
    family_weights={
        "quality": 0.35,
        "financial_health": 0.20,
        "growth": 0.25,
        "valuation": 0.20,
    },
    minimum_families=3,
    evaluation_start=date(2016, 1, 1),
)


def add_long_growth_v1_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the immutable long-growth V1 composite and governance flags."""

    result = frame.copy()

    weighted_sum = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in LONG_GROWTH_V1.family_weights.items():
        column = f"{family}_score"
        if column not in result.columns:
            raise ValueError(f"Missing required family score column: {column}")

        values = pd.to_numeric(result[column], errors="coerce")
        available = values.notna()

        weighted_sum.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        available_count.loc[available] += 1

    eligible = (
        (available_count >= LONG_GROWTH_V1.minimum_families)
        & (available_weight > 0)
    )

    composite = pd.Series(np.nan, index=result.index, dtype="float64")
    composite.loc[eligible] = (
        weighted_sum.loc[eligible]
        / available_weight.loc[eligible]
    )

    result["long_growth_v1_family_count"] = available_count
    result["long_growth_v1_weight_coverage"] = available_weight
    result["long_growth_v1_eligible"] = eligible
    result["long_growth_v1_score"] = composite

    full_family_coverage = available_count.eq(len(LONG_GROWTH_V1.family_weights))
    result["full_family_coverage"] = full_family_coverage
    result["top_conviction_eligible"] = (
        composite.notna()
        & full_family_coverage
    )

    result["health_missing"] = pd.to_numeric(
        result["financial_health_score"],
        errors="coerce",
    ).isna()
    result["growth_missing"] = pd.to_numeric(
        result["growth_score"],
        errors="coerce",
    ).isna()
    result["valuation_missing"] = pd.to_numeric(
        result["valuation_score"],
        errors="coerce",
    ).isna()

    decision_date = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )
    evaluation_start = pd.Timestamp(LONG_GROWTH_V1.evaluation_start)
    result["evaluation_eligible"] = (
        composite.notna()
        & decision_date.notna()
        & (decision_date >= evaluation_start)
    )

    result["model_id"] = LONG_GROWTH_V1.model_id

    return result
