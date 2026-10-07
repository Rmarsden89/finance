import pandas as pd
import pytest

from scripts.run_v5_fund_exp_003 import (
    add_exp_003_score,
    interaction_severity,
)


def _config():
    return {
        "base_family_weights": {
            "quality": 0.35,
            "financial_health": 0.20,
            "growth": 0.35,
            "valuation": 0.10,
        },
        "modifier": {
            "neutral_score": 50.0,
            "maximum_growth_to_valuation_shift": 0.10,
        },
        "minimum_families": 3,
        "top_conviction_requires_full_family_coverage": True,
    }


def test_severity_is_zero_without_mismatch() -> None:
    growth = pd.Series([40.0, 80.0, 50.0])
    valuation = pd.Series([20.0, 60.0, 50.0])
    result = interaction_severity(growth, valuation, 50.0)
    assert result.tolist() == pytest.approx([0.0, 0.0, 0.0])


def test_mid_extreme_interpolates_weights_smoothly() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 75.0,
        "valuation_score": 25.0,
    }])
    result = add_exp_003_score(frame, _config())
    row = result.iloc[0]
    assert row["v5_exp_003_interaction_severity"] == pytest.approx(0.25)
    assert row["v5_exp_003_growth_weight"] == pytest.approx(0.325)
    assert row["v5_exp_003_valuation_weight"] == pytest.approx(0.125)


def test_full_extreme_reaches_v1_growth_valuation_balance() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 100.0,
        "valuation_score": 0.0,
    }])
    result = add_exp_003_score(frame, _config())
    row = result.iloc[0]
    assert row["v5_exp_003_interaction_severity"] == pytest.approx(1.0)
    assert row["v5_exp_003_growth_weight"] == pytest.approx(0.25)
    assert row["v5_exp_003_valuation_weight"] == pytest.approx(0.20)
