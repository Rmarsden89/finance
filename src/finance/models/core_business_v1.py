from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CompositeModelDefinition:
    model_id: str
    family_weights: dict[str, float]
    minimum_families: int
    evaluation_start: date


CORE_BUSINESS_V1 = CompositeModelDefinition(
    model_id="core_business_v1",
    family_weights={
        "quality": 0.45,
        "financial_health": 0.25,
        "growth": 0.30,
    },
    minimum_families=2,
    evaluation_start=date(2016, 1, 1),
)


def add_core_business_v1_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the immutable V1 core-business composite and governance flags."""

    result = frame.copy()

    weighted_sum = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in CORE_BUSINESS_V1.family_weights.items():
        column = f"{family}_score"
        if column not in result.columns:
            raise ValueError(f"Missing required family score column: {column}")

        values = pd.to_numeric(result[column], errors="coerce")
        available = values.notna()

        weighted_sum.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        available_count.loc[available] += 1

    eligible = (
        (available_count >= CORE_BUSINESS_V1.minimum_families)
        & (available_weight > 0)
    )

    composite = pd.Series(np.nan, index=result.index, dtype="float64")
    composite.loc[eligible] = (
        weighted_sum.loc[eligible] / available_weight.loc[eligible]
    )

    result["core_business_v1_family_count"] = available_count
    result["core_business_v1_weight_coverage"] = available_weight
    result["core_business_v1_eligible"] = eligible
    result["core_business_v1_score"] = composite

    health_missing = pd.to_numeric(
        result["financial_health_score"],
        errors="coerce",
    ).isna()
    result["health_missing"] = health_missing

    result["top_conviction_eligible"] = (
        composite.notna()
        & ~health_missing
    )

    decision_date = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )
    evaluation_start = pd.Timestamp(CORE_BUSINESS_V1.evaluation_start)
    result["evaluation_eligible"] = (
        composite.notna()
        & decision_date.notna()
        & (decision_date >= evaluation_start)
    )

    result["model_id"] = CORE_BUSINESS_V1.model_id

    return result
