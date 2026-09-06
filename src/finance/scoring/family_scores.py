from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FamilyDefinition:
    name: str
    weights: dict[str, float]
    minimum_factors: int


FAMILY_DEFINITIONS: dict[str, FamilyDefinition] = {
    "quality": FamilyDefinition(
        name="quality",
        weights={
            "return_on_assets": 0.35,
            "return_on_equity": 0.15,
            "operating_margin": 0.25,
            "free_cash_flow_margin": 0.25,
        },
        minimum_factors=2,
    ),
    "financial_health": FamilyDefinition(
        name="financial_health",
        weights={
            "liabilities_to_assets": 0.35,
            "cash_to_assets": 0.30,
            "operating_cash_flow_to_liabilities": 0.35,
        },
        minimum_factors=2,
    ),
    "growth": FamilyDefinition(
        name="growth",
        weights={
            "revenue_growth_1y": 0.30,
            "net_income_growth_1y": 0.20,
            "operating_income_growth_1y": 0.20,
            "operating_cash_flow_growth_1y": 0.30,
        },
        minimum_factors=2,
    ),
}


def add_family_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add weighted family scores using available normalized factors only.

    Missing factor scores are ignored and the remaining weights are
    renormalized proportionally. A family is scored only when at least the
    configured minimum number of component factors is available.

    Positive operating cash flow is intentionally retained as a separate
    diagnostic flag and is not included in the financial-health weighted score.
    """

    result = frame.copy()

    for family_name, definition in FAMILY_DEFINITIONS.items():
        score_columns = {
            factor: f"{factor}_score"
            for factor in definition.weights
        }

        missing_columns = [
            column
            for column in score_columns.values()
            if column not in result.columns
        ]
        if missing_columns:
            raise ValueError(
                f"{family_name} scoring missing normalized columns: "
                + ", ".join(sorted(missing_columns))
            )

        weighted_sum = pd.Series(0.0, index=result.index, dtype="float64")
        available_weight = pd.Series(0.0, index=result.index, dtype="float64")
        available_count = pd.Series(0, index=result.index, dtype="int64")

        for factor, weight in definition.weights.items():
            values = pd.to_numeric(
                result[score_columns[factor]],
                errors="coerce",
            )
            available = values.notna()

            weighted_sum.loc[available] += values.loc[available] * weight
            available_weight.loc[available] += weight
            available_count.loc[available] += 1

        eligible = (
            (available_count >= definition.minimum_factors)
            & (available_weight > 0)
        )

        family_score = pd.Series(
            np.nan,
            index=result.index,
            dtype="float64",
        )
        family_score.loc[eligible] = (
            weighted_sum.loc[eligible]
            / available_weight.loc[eligible]
        )

        result[f"{family_name}_factor_count"] = available_count
        result[f"{family_name}_weight_coverage"] = available_weight
        result[f"{family_name}_eligible"] = eligible
        result[f"{family_name}_score"] = family_score

    if "positive_operating_cash_flow_validated" in result.columns:
        positive_ocf = pd.to_numeric(
            result["positive_operating_cash_flow_validated"],
            errors="coerce",
        )
        result["positive_operating_cash_flow_flag"] = positive_ocf
    elif "positive_operating_cash_flow" in result.columns:
        positive_ocf = pd.to_numeric(
            result["positive_operating_cash_flow"],
            errors="coerce",
        )
        result["positive_operating_cash_flow_flag"] = positive_ocf

    return result
