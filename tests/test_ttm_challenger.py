from __future__ import annotations

import pandas as pd

from finance.research.ttm_challenger import add_long_growth_v2_ttm_scores
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER


def test_ttm_challenger_substitutes_only_valuation_family() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": "AAA",
            "quality_score": 80.0,
            "financial_health_score": 70.0,
            "growth_score": 60.0,
            "valuation_score": 50.0,
            "ttm_valuation_score": 90.0,
        }
    ])

    result = add_long_growth_v2_ttm_scores(frame)

    expected = (
        80.0 * 0.35
        + 70.0 * 0.20
        + 60.0 * 0.25
        + 90.0 * 0.20
    )
    assert result.loc[0, f"{TTM_CHALLENGER.model_id}_score"] == expected
    assert result.loc[0, "quality_score"] == 80.0
    assert result.loc[0, "financial_health_score"] == 70.0
    assert result.loc[0, "growth_score"] == 60.0
    assert result.loc[0, "valuation_score"] == 50.0
    assert bool(result.loc[0, "v2_top_conviction_eligible"])


def test_ttm_challenger_preserves_v1_missing_family_reweighting_rule() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": "AAA",
            "quality_score": 80.0,
            "financial_health_score": 70.0,
            "growth_score": float("nan"),
            "valuation_score": 50.0,
            "ttm_valuation_score": 90.0,
        }
    ])

    result = add_long_growth_v2_ttm_scores(frame)

    numerator = 80.0 * 0.35 + 70.0 * 0.20 + 90.0 * 0.20
    denominator = 0.35 + 0.20 + 0.20
    assert result.loc[0, f"{TTM_CHALLENGER.model_id}_score"] == numerator / denominator
    assert bool(result.loc[0, f"{TTM_CHALLENGER.model_id}_eligible"])
    assert not bool(result.loc[0, "v2_top_conviction_eligible"])


def test_ttm_challenger_requires_three_families() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": "AAA",
            "quality_score": 80.0,
            "financial_health_score": float("nan"),
            "growth_score": float("nan"),
            "valuation_score": 50.0,
            "ttm_valuation_score": 90.0,
        }
    ])

    result = add_long_growth_v2_ttm_scores(frame)

    assert not bool(result.loc[0, f"{TTM_CHALLENGER.model_id}_eligible"])
    assert pd.isna(result.loc[0, f"{TTM_CHALLENGER.model_id}_score"])
    assert not bool(result.loc[0, "v2_top_conviction_eligible"])
