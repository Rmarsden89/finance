from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from finance.factors.stability import add_stability_factors
from finance.factors.validation import validate_raw_factors


def _weekly_prices(
    *,
    ticker: str = "AAA",
    weeks: int = 60,
    start_price: float = 100.0,
    weekly_change: float = 0.0,
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


def test_stability_factors_for_constant_price_series_are_zero() -> None:
    frame = _weekly_prices(weeks=60, weekly_change=0.0)
    result = validate_raw_factors(add_stability_factors(frame))
    last = result.iloc[-1]

    assert last["stability_return_count_52w"] == 52
    assert last["volatility_52w"] == 0.0
    assert last["downside_deviation_52w"] == 0.0
    assert last["max_drawdown_52w"] == 0.0
    assert last["volatility_52w_valid"]
    assert last["downside_deviation_52w_valid"]
    assert last["max_drawdown_52w_valid"]


def test_stability_detects_persistent_decline() -> None:
    frame = _weekly_prices(weeks=60, weekly_change=-0.01)
    result = validate_raw_factors(add_stability_factors(frame))
    last = result.iloc[-1]

    assert last["volatility_52w"] >= 0.0
    assert last["downside_deviation_52w"] > 0.0
    assert last["max_drawdown_52w"] > 0.0
    assert last["max_drawdown_52w"] < 1.0


def test_stability_resets_across_membership_gap() -> None:
    frame = _weekly_prices(weeks=45)
    gap_start = frame.iloc[-1]["decision_date"] + timedelta(days=35)

    tail = _weekly_prices(
        ticker="AAA",
        weeks=10,
        start_price=120.0,
    )
    tail["decision_date"] = [
        gap_start + timedelta(days=7 * i)
        for i in range(len(tail))
    ]
    combined = pd.concat([frame, tail], ignore_index=True)

    result = add_stability_factors(combined)
    last = result.iloc[-1]

    assert last["stability_return_count_52w"] < 40
    assert pd.isna(last["volatility_52w"])
    assert pd.isna(last["downside_deviation_52w"])
    assert pd.isna(last["max_drawdown_52w"])


def test_stability_resets_when_price_source_changes() -> None:
    frame = _weekly_prices(weeks=45, source="tiingo")
    tail = _weekly_prices(weeks=10, source="stooq_bulk")
    tail["decision_date"] = [
        frame.iloc[-1]["decision_date"] + timedelta(days=7 * (i + 1))
        for i in range(len(tail))
    ]
    combined = pd.concat([frame, tail], ignore_index=True)

    result = add_stability_factors(combined)
    first_new_source = result.iloc[45]
    last = result.iloc[-1]

    assert first_new_source["stability_source_change"]
    assert pd.isna(first_new_source["weekly_return"])
    assert last["stability_return_count_52w"] < 40
    assert pd.isna(last["volatility_52w"])
