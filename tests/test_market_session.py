from datetime import date, datetime
from zoneinfo import ZoneInfo

from finance.broker.market_session import evaluate_nyse_session


ET = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def test_normal_trading_day_is_ready_during_regular_session() -> None:
    gate = evaluate_nyse_session(
        date(2026, 9, 15),
        now=_et(2026, 9, 15, 10, 30),
    )

    assert gate.is_trading_day
    assert gate.ready
    assert gate.within_regular_session
    assert not gate.early_close
    assert gate.reason == "regular_session_open"
    assert gate.market_open is not None and "09:30:00" in gate.market_open
    assert gate.market_close is not None and "16:00:00" in gate.market_close


def test_weekend_is_blocked() -> None:
    gate = evaluate_nyse_session(
        date(2026, 9, 19),
        now=_et(2026, 9, 19, 10, 30),
    )

    assert not gate.is_trading_day
    assert not gate.ready
    assert gate.reason == "not_a_nyse_trading_day"


def test_full_market_holiday_is_blocked() -> None:
    gate = evaluate_nyse_session(
        date(2026, 12, 25),
        now=_et(2026, 12, 25, 10, 30),
    )

    assert not gate.is_trading_day
    assert not gate.ready
    assert gate.reason == "not_a_nyse_trading_day"


def test_early_close_day_uses_shortened_session() -> None:
    as_of = date(2026, 11, 27)  # Friday after Thanksgiving

    open_gate = evaluate_nyse_session(as_of, now=_et(2026, 11, 27, 12, 30))
    assert open_gate.is_trading_day
    assert open_gate.early_close
    assert open_gate.ready
    assert open_gate.market_close is not None and "13:00:00" in open_gate.market_close

    closed_gate = evaluate_nyse_session(as_of, now=_et(2026, 11, 27, 13, 1))
    assert closed_gate.early_close
    assert not closed_gate.ready
    assert closed_gate.reason == "after_regular_session"


def test_before_open_is_blocked() -> None:
    gate = evaluate_nyse_session(
        date(2026, 9, 15),
        now=_et(2026, 9, 15, 9, 29),
    )

    assert gate.is_trading_day
    assert not gate.ready
    assert gate.reason == "before_regular_session"


def test_as_of_must_match_current_market_date() -> None:
    gate = evaluate_nyse_session(
        date(2026, 9, 15),
        now=_et(2026, 9, 16, 10, 30),
    )

    assert gate.is_trading_day
    assert not gate.ready
    assert gate.reason == "as_of_does_not_match_current_market_date"
