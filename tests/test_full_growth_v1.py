from __future__ import annotations

import math

import pandas as pd

from finance.models.full_growth_v1 import add_full_growth_v1_scores


def test_full_growth_v1_uses_all_available_family_weights() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": 50.0,
        "stability_score": 90.0,
        "momentum_score": 40.0,
    }])

    result = add_full_growth_v1_scores(frame)

    expected = (
        80.0 * 0.30
        + 70.0 * 0.20
        + 60.0 * 0.20
        + 50.0 * 0.15
        + 90.0 * 0.10
        + 40.0 * 0.05
    )

    assert math.isclose(result.loc[0, "full_growth_v1_score"], expected)
    assert result.loc[0, "full_growth_v1_family_count"] == 6
    assert result.loc[0, "full_growth_v1_supporting_family_count"] == 3
    assert result.loc[0, "full_growth_v1_eligible"]
    assert result.loc[0, "top_conviction_eligible"]
    assert result.loc[0, "full_family_coverage"]
    assert result.loc[0, "evaluation_eligible"]
    assert result.loc[0, "model_id"] == "full_growth_v1"


def test_momentum_can_contribute_but_cannot_rescue_eligibility() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": float("nan"),
        "growth_score": 60.0,
        "valuation_score": float("nan"),
        "stability_score": 90.0,
        "momentum_score": 100.0,
    }])

    result = add_full_growth_v1_scores(frame)

    assert result.loc[0, "full_growth_v1_supporting_family_count"] == 1
    assert not result.loc[0, "full_growth_v1_eligible"]
    assert pd.isna(result.loc[0, "full_growth_v1_score"])


def test_full_growth_v1_requires_quality_and_growth() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": float("nan"),
        "financial_health_score": 80.0,
        "growth_score": 70.0,
        "valuation_score": 60.0,
        "stability_score": 90.0,
        "momentum_score": 100.0,
    }])

    result = add_full_growth_v1_scores(frame)

    assert result.loc[0, "full_growth_v1_supporting_family_count"] == 3
    assert not result.loc[0, "full_growth_v1_eligible"]
    assert pd.isna(result.loc[0, "full_growth_v1_score"])


def test_top_conviction_requires_five_core_families_but_not_momentum() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": 50.0,
        "stability_score": 90.0,
        "momentum_score": float("nan"),
    }])

    result = add_full_growth_v1_scores(frame)

    expected = (
        80.0 * 0.30
        + 70.0 * 0.20
        + 60.0 * 0.20
        + 50.0 * 0.15
        + 90.0 * 0.10
    ) / 0.95

    assert math.isclose(result.loc[0, "full_growth_v1_score"], expected)
    assert result.loc[0, "full_growth_v1_eligible"]
    assert result.loc[0, "top_conviction_eligible"]
    assert not result.loc[0, "full_family_coverage"]
    assert result.loc[0, "momentum_missing"]


def test_top_conviction_fails_when_stability_missing() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": 50.0,
        "stability_score": float("nan"),
        "momentum_score": 100.0,
    }])

    result = add_full_growth_v1_scores(frame)

    assert result.loc[0, "full_growth_v1_eligible"]
    assert not result.loc[0, "top_conviction_eligible"]


def test_full_growth_v1_excludes_2015_from_evaluation() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2015-12-25",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": 50.0,
        "stability_score": 90.0,
        "momentum_score": 40.0,
    }])

    result = add_full_growth_v1_scores(frame)

    assert result.loc[0, "full_growth_v1_eligible"]
    assert pd.notna(result.loc[0, "full_growth_v1_score"])
    assert not result.loc[0, "evaluation_eligible"]
