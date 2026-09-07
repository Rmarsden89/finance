from __future__ import annotations

import math

import pandas as pd

from finance.models.long_growth_v1 import add_long_growth_v1_scores


def test_long_growth_v1_uses_four_family_weights() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": 50.0,
    }])

    result = add_long_growth_v1_scores(frame)

    expected = (
        80.0 * 0.35
        + 70.0 * 0.20
        + 60.0 * 0.25
        + 50.0 * 0.20
    )

    assert math.isclose(result.loc[0, "long_growth_v1_score"], expected)
    assert result.loc[0, "long_growth_v1_family_count"] == 4
    assert result.loc[0, "full_family_coverage"]
    assert result.loc[0, "top_conviction_eligible"]
    assert result.loc[0, "evaluation_eligible"]
    assert result.loc[0, "model_id"] == "long_growth_v1"


def test_long_growth_v1_reweights_one_missing_family_but_not_top_conviction() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2020-01-03",
        "quality_score": 80.0,
        "financial_health_score": 70.0,
        "growth_score": 60.0,
        "valuation_score": float("nan"),
    }])

    result = add_long_growth_v1_scores(frame)

    expected = (
        80.0 * 0.35
        + 70.0 * 0.20
        + 60.0 * 0.25
    ) / (0.35 + 0.20 + 0.25)

    assert math.isclose(result.loc[0, "long_growth_v1_score"], expected)
    assert result.loc[0, "long_growth_v1_family_count"] == 3
    assert not result.loc[0, "full_family_coverage"]
    assert not result.loc[0, "top_conviction_eligible"]
    assert result.loc[0, "valuation_missing"]
    assert result.loc[0, "evaluation_eligible"]


def test_long_growth_v1_requires_three_families_and_excludes_2015() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2015-06-05",
            "quality_score": 80.0,
            "financial_health_score": 70.0,
            "growth_score": 60.0,
            "valuation_score": float("nan"),
        },
        {
            "decision_date": "2020-01-03",
            "quality_score": 80.0,
            "financial_health_score": float("nan"),
            "growth_score": float("nan"),
            "valuation_score": 50.0,
        },
    ])

    result = add_long_growth_v1_scores(frame)

    assert pd.notna(result.loc[0, "long_growth_v1_score"])
    assert not result.loc[0, "evaluation_eligible"]

    assert pd.isna(result.loc[1, "long_growth_v1_score"])
    assert not result.loc[1, "long_growth_v1_eligible"]
    assert not result.loc[1, "evaluation_eligible"]
