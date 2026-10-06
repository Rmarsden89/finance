import pandas as pd
import pytest

from scripts.evaluate_v5_fund_cand_003 import (
    add_candidate_score,
    interaction_severity,
    mean_replacement_rate,
    rolling_gate_summary,
)


def _config() -> dict:
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


def test_smooth_interaction_weights_mid_extreme() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 75.0,
        "valuation_score": 25.0,
    }])
    result = add_candidate_score(frame, _config())
    row = result.iloc[0]
    assert row["v5_candidate_interaction_severity"] == pytest.approx(0.25)
    assert row["v5_candidate_growth_weight"] == pytest.approx(0.325)
    assert row["v5_candidate_valuation_weight"] == pytest.approx(0.125)


def test_smooth_interaction_zero_when_not_mismatched() -> None:
    severity = interaction_severity(
        pd.Series([40.0, 80.0]),
        pd.Series([20.0, 60.0]),
        50.0,
    )
    assert severity.tolist() == pytest.approx([0.0, 0.0])


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
