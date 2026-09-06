from __future__ import annotations

import math

import pandas as pd

from finance.models.core_business_v1 import add_core_business_v1_scores


def test_core_business_v1_reweights_available_families() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2018-01-05",
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": float("nan"),
    }])

    result = add_core_business_v1_scores(frame)

    expected = (80.0 * 0.45 + 60.0 * 0.25) / (0.45 + 0.25)

    assert math.isclose(result.loc[0, "core_business_v1_score"], expected)
    assert result.loc[0, "core_business_v1_family_count"] == 2
    assert not result.loc[0, "health_missing"]
    assert result.loc[0, "top_conviction_eligible"]
    assert result.loc[0, "evaluation_eligible"]
    assert result.loc[0, "model_id"] == "core_business_v1"


def test_core_business_v1_marks_missing_health_and_blocks_top_conviction() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2018-01-05",
        "quality_score": 75.0,
        "financial_health_score": float("nan"),
        "growth_score": 70.0,
    }])

    result = add_core_business_v1_scores(frame)

    assert result.loc[0, "core_business_v1_score"] == (
        75.0 * 0.45 + 70.0 * 0.30
    ) / (0.45 + 0.30)
    assert result.loc[0, "health_missing"]
    assert not result.loc[0, "top_conviction_eligible"]
    assert result.loc[0, "evaluation_eligible"]


def test_core_business_v1_requires_two_families_and_excludes_2015_from_evaluation() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2015-06-05",
            "quality_score": 80.0,
            "financial_health_score": 70.0,
            "growth_score": float("nan"),
        },
        {
            "decision_date": "2018-01-05",
            "quality_score": 80.0,
            "financial_health_score": float("nan"),
            "growth_score": float("nan"),
        },
    ])

    result = add_core_business_v1_scores(frame)

    assert pd.notna(result.loc[0, "core_business_v1_score"])
    assert not result.loc[0, "evaluation_eligible"]

    assert pd.isna(result.loc[1, "core_business_v1_score"])
    assert not result.loc[1, "core_business_v1_eligible"]
    assert not result.loc[1, "evaluation_eligible"]
