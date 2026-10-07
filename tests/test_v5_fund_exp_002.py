import pandas as pd
import pytest

from scripts.run_v5_fund_exp_002 import add_exp_002_score


def _config():
    return {
        "default_family_weights": {
            "quality": 0.35,
            "financial_health": 0.20,
            "growth": 0.35,
            "valuation": 0.10,
        },
        "modifier": {
            "growth_score_min": 75.0,
            "valuation_score_max": 25.0,
            "family_weights_when_triggered": {
                "quality": 0.35,
                "financial_health": 0.20,
                "growth": 0.25,
                "valuation": 0.20,
            },
        },
        "minimum_families": 3,
        "top_conviction_requires_full_family_coverage": True,
    }


def test_modifier_uses_v1_balance_in_extreme_cell() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 90.0,
        "valuation_score": 20.0,
    }])
    result = add_exp_002_score(frame, _config())
    expected = 80*.35 + 60*.20 + 90*.25 + 20*.20
    assert bool(result.iloc[0]["v5_exp_002_modifier_triggered"]) is True
    assert result.iloc[0]["v5_exp_002_score"] == pytest.approx(expected)


def test_default_growth_heavy_balance_outside_extreme_cell() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 90.0,
        "valuation_score": 40.0,
    }])
    result = add_exp_002_score(frame, _config())
    expected = 80*.35 + 60*.20 + 90*.35 + 40*.10
    assert bool(result.iloc[0]["v5_exp_002_modifier_triggered"]) is False
    assert result.iloc[0]["v5_exp_002_score"] == pytest.approx(expected)
