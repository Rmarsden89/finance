from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from finance.factors.momentum import add_momentum_factors
from finance.factors.validation import validate_raw_factors


def _weekly_prices(
    *,
    ticker: str = "AAA",
    weeks: int = 60,
    start_price: float = 100.0,
    weekly_change: float = 0.01,
    source: str = "tiingo",
    basis: str = "adjusted_close",
) -> pd.DataFrame:
    start = date(2024, 1, 5)
    rows = []
    price = start_price
    for week in range(weeks):
        if week > 0:
            price *= 1.0 + weekly_change
        rows.append({
            "ticker": ticker,
            "decision_date": start + timedelta(days=7 * week),
            "return_price": price,
            "price_source": source,
            "return_price_basis": basis,
        })
    return pd.DataFrame(rows)


def test_momentum_uses_12m_and_6m_anchors_excluding_recent_month() -> None:
    frame = _weekly_prices(weeks=60, weekly_change=0.01)
    result = validate_raw_factors(add_momentum_factors(frame))
    last = result.iloc[-1]

    expected_12m = (1.01 ** (52 - 4)) - 1.0
    expected_6m = (1.01 ** (26 - 4)) - 1.0

    assert abs(last["momentum_12m_ex_1m"] - expected_12m) < 1e-12
    assert abs(last["momentum_6m_ex_1m"] - expected_6m) < 1e-12
    assert last["momentum_12m_lookback_valid"]
    assert last["momentum_6m_lookback_valid"]
    assert last["momentum_12m_ex_1m_valid"]
    assert last["momentum_6m_ex_1m_valid"]


def test_momentum_requires_sufficient_calendar_history() -> None:
    frame = _weekly_prices(weeks=30)
    result = add_momentum_factors(frame)
    last = result.iloc[-1]

    assert pd.isna(last["momentum_12m_ex_1m"])
    assert pd.notna(last["momentum_6m_ex_1m"])


def test_momentum_resets_across_membership_gap() -> None:
    frame = _weekly_prices(weeks=55)
    gap_start = frame.iloc[-1]["decision_date"] + timedelta(days=35)

    tail = _weekly_prices(weeks=10, start_price=150.0)
    tail["decision_date"] = [
        gap_start + timedelta(days=7 * i)
        for i in range(len(tail))
    ]
    combined = pd.concat([frame, tail], ignore_index=True)

    result = add_momentum_factors(combined)
    last = result.iloc[-1]

    assert pd.isna(last["momentum_12m_ex_1m"])
    assert pd.isna(last["momentum_6m_ex_1m"])


def test_momentum_resets_when_price_source_changes() -> None:
    frame = _weekly_prices(weeks=55, source="tiingo")
    tail = _weekly_prices(weeks=10, source="stooq_bulk")
    tail["decision_date"] = [
        frame.iloc[-1]["decision_date"] + timedelta(days=7 * (i + 1))
        for i in range(len(tail))
    ]
    combined = pd.concat([frame, tail], ignore_index=True)

    result = add_momentum_factors(combined)

    assert result.iloc[55]["momentum_source_change"]
    assert pd.isna(result.iloc[-1]["momentum_12m_ex_1m"])
    assert pd.isna(result.iloc[-1]["momentum_6m_ex_1m"])
