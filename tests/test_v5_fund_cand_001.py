import pandas as pd
import pytest

from scripts.evaluate_v5_fund_cand_001 import (
    mean_replacement_rate,
    rolling_gate_summary,
)


def test_mean_replacement_rate_is_week_to_week_turnover() -> None:
    sets = {
        "2026-01-02": list("ABCDEFGHIJ"),
        "2026-01-09": list("ABCDEFGHIK"),
        "2026-01-16": list("ABCDEFGHLM"),
    }
    assert mean_replacement_rate(sets) == pytest.approx(0.15)


def test_rolling_gate_summary_uses_xirr_wins_and_benchmark_counts() -> None:
    frame = pd.DataFrame([
        {
            "window_years": 3,
            "xirr_delta": 0.01,
            "v1_beats_benchmark_xirr": True,
            "candidate_beats_benchmark_xirr": True,
        },
        {
            "window_years": 3,
            "xirr_delta": -0.01,
            "v1_beats_benchmark_xirr": False,
            "candidate_beats_benchmark_xirr": True,
        },
        {
            "window_years": 5,
            "xirr_delta": 0.02,
            "v1_beats_benchmark_xirr": True,
            "candidate_beats_benchmark_xirr": True,
        },
    ])
    result = rolling_gate_summary(frame)
    assert result["3y"]["xirr_win_rate"] == pytest.approx(0.5)
    assert result["3y"]["candidate_benchmark_beating_count"] == 2
    assert result["5y"]["median_xirr_delta"] == pytest.approx(0.02)
