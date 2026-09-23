from __future__ import annotations

import pandas as pd

from scripts.audit_v2_ttm_market_sensitivity import _selection_comparison


def _panel(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": ticker,
            "score": float(100 - index),
            "eligible": True,
        }
        for index, ticker in enumerate(symbols)
    ])


def test_selection_comparison_reports_single_replacement() -> None:
    before = _panel([f"T{n:02d}" for n in range(1, 12)])
    after = before.copy()
    after.loc[after["ticker"].eq("T11"), "score"] = 200.0

    result = _selection_comparison(
        before,
        after,
        score_column="score",
        eligible_column="eligible",
    )

    row = result.iloc[0]
    assert bool(row["membership_changed"])
    assert bool(row["order_changed"])
    assert row["before_count"] == 10
    assert row["after_count"] == 10
    assert row["top10_overlap"] == 9
    assert row["entered"] == "T11"
    assert row["exited"] == "T10"


def test_selection_comparison_ignores_order_only_change_for_membership() -> None:
    before = _panel([f"T{n:02d}" for n in range(1, 11)])
    after = before.copy()
    after.loc[after["ticker"].eq("T01"), "score"] = 50.0
    after.loc[after["ticker"].eq("T02"), "score"] = 200.0

    result = _selection_comparison(
        before,
        after,
        score_column="score",
        eligible_column="eligible",
    )

    row = result.iloc[0]
    assert not bool(row["membership_changed"])
    assert bool(row["order_changed"])
    assert row["top10_overlap"] == 10
    assert row["entered"] == ""
    assert row["exited"] == ""
