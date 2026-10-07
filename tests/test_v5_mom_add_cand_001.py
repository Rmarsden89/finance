import pandas as pd
import pytest

from scripts.evaluate_v5_mom_add_cand_001 import add_candidate_score


def test_additive_momentum_candidate_score() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 75.0,
        "valuation_score": 25.0,
        "momentum_score": 90.0,
    }])
    base_config = {
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
    momentum_config = {
        "candidate_weight": 0.95,
        "momentum_weight": 0.05,
    }
    result = add_candidate_score(
        frame,
        base_config=base_config,
        momentum_config=momentum_config,
    )
    # At 75/25, severity=.25 => growth .325, valuation .125.
    base = 80*.35 + 60*.20 + 75*.325 + 25*.125
    expected = .95*base + .05*90
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)
    assert bool(result.iloc[0]["v5_candidate_top_conviction_eligible"]) is True
