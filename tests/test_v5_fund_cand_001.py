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


from scripts.evaluate_v5_fund_cand_001 import reconstruct_holding_path


def test_reconstruct_holding_path_tracks_position_weights() -> None:
    class Quote:
        mark_price = 10.0

    class Store:
        def latest_as_of(self, ticker, as_of):
            return Quote()

    trades = pd.DataFrame([
        {
            "decision_date": pd.Timestamp("2026-01-02").date(),
            "ticker": "AAA",
            "side": "buy",
            "units": 1.0,
        },
        {
            "decision_date": pd.Timestamp("2026-01-02").date(),
            "ticker": "BBB",
            "side": "buy",
            "units": 1.0,
        },
    ])
    weekly = pd.DataFrame([
        {
            "decision_date": pd.Timestamp("2026-01-02").date(),
            "valuation_date": pd.Timestamp("2026-01-03").date(),
            "cash": 0.0,
        }
    ])

    result = reconstruct_holding_path(trades, store=Store(), weekly=weekly)
    assert result.iloc[0]["largest_position_weight"] == pytest.approx(0.5)
    assert result.iloc[0]["top5_position_weight"] == pytest.approx(1.0)


from scripts.evaluate_v5_fund_cand_001 import add_candidate_score


def test_frozen_candidate_score_matches_declared_weights() -> None:
    frame = pd.DataFrame([{
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 100.0,
        "valuation_score": 20.0,
    }])
    config = {
        "family_weights": {
            "quality": 0.35,
            "financial_health": 0.20,
            "growth": 0.35,
            "valuation": 0.10,
        },
        "minimum_families": 3,
        "top_conviction_requires_full_family_coverage": True,
    }
    result = add_candidate_score(frame, config)
    expected = 80*.35 + 60*.20 + 100*.35 + 20*.10
    assert result.iloc[0]["v5_candidate_score"] == pytest.approx(expected)


import json
from datetime import date

from scripts.evaluate_v5_fund_cand_001 import serializable_concentration_row


def test_concentration_max_row_dates_are_serializable() -> None:
    row = pd.Series({
        "decision_date": date(2026, 1, 2),
        "valuation_date": date(2026, 1, 3),
        "largest_position_ticker": "AAA",
        "largest_position_weight": 0.25,
    })
    payload = serializable_concentration_row(row)
    assert payload["decision_date"] == "2026-01-02"
    assert payload["valuation_date"] == "2026-01-03"
    json.dumps(payload)
