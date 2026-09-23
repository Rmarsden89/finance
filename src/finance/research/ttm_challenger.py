from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from finance.research.ttm_promotion_criteria import TTM_CHALLENGER


@dataclass(frozen=True)
class TtmCompositeAudit:
    rows: int
    eligible_rows: int
    top_conviction_rows: int


def add_long_growth_v2_ttm_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Build the frozen V2 challenger by substituting only TTM Valuation."""

    result = frame.copy()
    family_columns = {
        "quality": "quality_score",
        "financial_health": "financial_health_score",
        "growth": "growth_score",
        "valuation": "ttm_valuation_score",
    }

    weighted_sum = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in TTM_CHALLENGER.family_weights:
        column = family_columns[family]
        if column not in result.columns:
            raise ValueError(f"Missing challenger family score column: {column}")
        values = pd.to_numeric(result[column], errors="coerce")
        available = values.notna()
        weighted_sum.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        available_count.loc[available] += 1

    eligible = (
        available_count.ge(TTM_CHALLENGER.minimum_families)
        & available_weight.gt(0)
    )
    composite = pd.Series(np.nan, index=result.index, dtype="float64")
    composite.loc[eligible] = (
        weighted_sum.loc[eligible] / available_weight.loc[eligible]
    )

    prefix = TTM_CHALLENGER.model_id
    result[f"{prefix}_family_count"] = available_count
    result[f"{prefix}_weight_coverage"] = available_weight
    result[f"{prefix}_eligible"] = eligible
    result[f"{prefix}_score"] = composite

    full_family_coverage = available_count.eq(len(family_columns))
    result["v2_full_family_coverage"] = full_family_coverage
    result["v2_top_conviction_eligible"] = (
        composite.notna() & full_family_coverage
    )

    decision_date = pd.to_datetime(
        result["decision_date"], errors="coerce"
    )
    result["v2_evaluation_eligible"] = (
        composite.notna()
        & decision_date.notna()
        & decision_date.ge(pd.Timestamp(TTM_CHALLENGER.evaluation_start))
    )
    result["v2_model_id"] = TTM_CHALLENGER.model_id
    return result


def audit_ttm_composite(frame: pd.DataFrame) -> TtmCompositeAudit:
    prefix = TTM_CHALLENGER.model_id
    return TtmCompositeAudit(
        rows=len(frame),
        eligible_rows=int(
            frame[f"{prefix}_eligible"].fillna(False).astype(bool).sum()
        ),
        top_conviction_rows=int(
            frame["v2_top_conviction_eligible"].fillna(False).astype(bool).sum()
        ),
    )
