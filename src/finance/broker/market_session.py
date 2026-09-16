from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TZ = ZoneInfo("America/New_York")


class MarketSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketSessionGate:
    exchange: str
    as_of: str
    timezone: str
    checked_at_utc: str
    checked_at_market_tz: str
    is_trading_day: bool
    market_open: str | None
    market_close: str | None
    early_close: bool
    within_regular_session: bool
    ready: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise MarketSessionError("Market-session clock must be timezone-aware")
    return current.astimezone(timezone.utc)


def evaluate_nyse_session(as_of: date, *, now: datetime | None = None) -> MarketSessionGate:
    """Resolve the NYSE regular-hours session for an approved V1 submission.

    This function deliberately fails closed if the maintained calendar dependency
    cannot resolve the requested date. Read-only and dry-run paths do not need to
    call this gate.
    """
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:  # pragma: no cover - environment/setup failure
        raise MarketSessionError(
            "pandas-market-calendars is required for approved V1 submission"
        ) from exc

    now_utc = _aware_utc(now)
    now_market = now_utc.astimezone(MARKET_TZ)

    try:
        calendar = mcal.get_calendar("NYSE")
        schedule = calendar.schedule(
            start_date=as_of.isoformat(),
            end_date=as_of.isoformat(),
        )
    except Exception as exc:
        raise MarketSessionError(
            f"Unable to resolve NYSE session for {as_of.isoformat()}: {exc}"
        ) from exc

    common = {
        "exchange": "NYSE",
        "as_of": as_of.isoformat(),
        "timezone": "America/New_York",
        "checked_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "checked_at_market_tz": now_market.isoformat(),
    }

    if schedule.empty:
        return MarketSessionGate(
            **common,
            is_trading_day=False,
            market_open=None,
            market_close=None,
            early_close=False,
            within_regular_session=False,
            ready=False,
            reason="not_a_nyse_trading_day",
        )

    if len(schedule) != 1:
        raise MarketSessionError(
            f"Unexpected NYSE schedule shape for {as_of.isoformat()}: {len(schedule)} rows"
        )

    row = schedule.iloc[0]
    try:
        market_open_utc = row["market_open"].to_pydatetime().astimezone(timezone.utc)
        market_close_utc = row["market_close"].to_pydatetime().astimezone(timezone.utc)
    except Exception as exc:
        raise MarketSessionError(
            f"NYSE schedule is missing a usable regular session for {as_of.isoformat()}"
        ) from exc

    market_open = market_open_utc.astimezone(MARKET_TZ)
    market_close = market_close_utc.astimezone(MARKET_TZ)
    early_close = (market_close.hour, market_close.minute) != (16, 0)

    same_market_date = now_market.date() == as_of
    within_session = same_market_date and market_open_utc <= now_utc < market_close_utc

    if not same_market_date:
        ready = False
        reason = "as_of_does_not_match_current_market_date"
    elif now_utc < market_open_utc:
        ready = False
        reason = "before_regular_session"
    elif now_utc >= market_close_utc:
        ready = False
        reason = "after_regular_session"
    else:
        ready = True
        reason = "regular_session_open"

    return MarketSessionGate(
        **common,
        is_trading_day=True,
        market_open=market_open.isoformat(),
        market_close=market_close.isoformat(),
        early_close=early_close,
        within_regular_session=within_session,
        ready=ready,
        reason=reason,
    )
