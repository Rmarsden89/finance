import pandas as pd
import pytest

from scripts.evaluate_v5_fund_cand_002 import (
    add_candidate_score,
    mean_replacement_rate,
    rolling_gate_summary,
)


def _config() -> dict:
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


def test_frozen_candidate_002_extreme_cell_uses_v1_balance() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 90.0,
        "valuation_score": 20.0,
    }])
    result = add_candidate_score(frame, _config())
    expected = 80*.35 + 60*.20 + 90*.25 + 20*.20
    assert bool(result.iloc[0]["v5_candidate_modifier_triggered"]) is True
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)


def test_frozen_candidate_002_default_uses_growth_heavy_balance() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 90.0,
        "valuation_score": 40.0,
    }])
    result = add_candidate_score(frame, _config())
    expected = 80*.35 + 60*.20 + 90*.35 + 40*.10
    assert bool(result.iloc[0]["v5_candidate_modifier_triggered"]) is False
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)


def test_replacement_rate_helper() -> None:
    sets = {
        "2026-01-02": list("ABCDEFGHIJ"),
        "2026-01-09": list("ABCDEFGHIK"),
    }
    assert mean_replacement_rate(sets) == pytest.approx(0.10)


def test_rolling_summary_helper() -> None:
    frame = pd.DataFrame([
        {
            "window_years": 3,
            "xirr_delta": 0.01,
            "v1_beats_benchmark_xirr": True,
            "candidate_beats_benchmark_xirr": True,
        },
        {
            "window_years": 5,
            "xirr_delta": 0.02,
            "v1_beats_benchmark_xirr": False,
            "candidate_beats_benchmark_xirr": True,
        },
    ])
    result = rolling_gate_summary(frame)
    assert result["3y"]["xirr_win_rate"] == pytest.approx(1.0)
    assert result["5y"]["candidate_benchmark_beating_count"] == 1
