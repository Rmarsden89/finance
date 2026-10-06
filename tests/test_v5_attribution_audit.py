from __future__ import annotations

import pandas as pd

from scripts.audit_v5_attribution_dataset import audit_dataset


def good_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "decision_date": "2026-01-02",
            "ticker": "AAA",
            "long_growth_v1_score": 90.0,
            "top_conviction_eligible": True,
            "v1_rank": 1,
            "v1_top10": True,
            "v1_top25": True,
            "v1_rank_band": "top10",
            "forward_entry_price": 100.0,
            "forward_entry_price_date": "2026-01-02",
            "spy_entry_price": 100.0,
            "spy_entry_price_date": "2026-01-02",
            "fwd_1w_return": 0.10,
            "fwd_1w_exit_date": "2026-01-09",
            "fwd_1w_spy_return": 0.05,
            "fwd_1w_spy_exit_date": "2026-01-09",
            "fwd_1w_excess_return": 0.05,
            "fwd_1w_status": "mature",
        },
        {
            "decision_date": "2026-01-02",
            "ticker": "BBB",
            "long_growth_v1_score": 80.0,
            "top_conviction_eligible": True,
            "v1_rank": 2,
            "v1_top10": True,
            "v1_top25": True,
            "v1_rank_band": "top10",
            "forward_entry_price": 100.0,
            "forward_entry_price_date": "2026-01-02",
            "spy_entry_price": 100.0,
            "spy_entry_price_date": "2026-01-02",
            "fwd_1w_return": None,
            "fwd_1w_exit_date": None,
            "fwd_1w_spy_return": 0.05,
            "fwd_1w_spy_exit_date": "2026-01-09",
            "fwd_1w_excess_return": None,
            "fwd_1w_status": "missing_exit_price",
        },
    ])


def test_good_contract_passes() -> None:
    result = audit_dataset(good_frame(), horizons_weeks=(1,), max_exit_delay_days=7)
    assert result["status"] == "PASS"
    assert result["contract_failures"] == 0


def test_future_entry_fails() -> None:
    frame = good_frame()
    frame.loc[0, "forward_entry_price_date"] = "2026-01-03"
    result = audit_dataset(frame, horizons_weeks=(1,), max_exit_delay_days=7)
    assert result["status"] == "FAIL"
    assert result["future_entry_dates"] == 1


def test_bad_excess_return_fails() -> None:
    frame = good_frame()
    frame.loc[0, "fwd_1w_excess_return"] = 0.99
    result = audit_dataset(frame, horizons_weeks=(1,), max_exit_delay_days=7)
    assert result["status"] == "FAIL"
    assert result["horizons"]["1"]["excess_return_mismatches"] == 1
