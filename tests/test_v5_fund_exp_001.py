import pandas as pd
import pytest

from scripts.run_v5_fund_exp_001 import add_candidate_score


def _config() -> dict:
    return {
        "family_weights": {
            "quality": 0.35,
            "financial_health": 0.20,
            "growth": 0.35,
            "valuation": 0.10,
        },
        "minimum_families": 3,
        "top_conviction_requires_full_family_coverage": True,
    }


def test_candidate_score_uses_declared_weights() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 100.0,
        "valuation_score": 20.0,
    }])
    result = add_candidate_score(frame, _config())
    expected = 80*.35 + 60*.20 + 100*.35 + 20*.10
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)
    assert bool(result.iloc[0]["v5_candidate_top_conviction_eligible"]) is True


def test_candidate_preserves_v1_missing_family_renormalization() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 100.0,
        "valuation_score": None,
    }])
    result = add_candidate_score(frame, _config())
    expected = (80*.35 + 60*.20 + 100*.35) / .90
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)
    assert bool(result.iloc[0]["v5_candidate_eligible"]) is True
    assert bool(result.iloc[0]["v5_candidate_top_conviction_eligible"]) is False
