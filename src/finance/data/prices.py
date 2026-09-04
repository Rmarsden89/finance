from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyPrice:
    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None
    adjusted_close: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class PriceCoverageResult:
    ticker: str
    requested_start: date
    requested_end: date
    first_price_date: date | None
    last_price_date: date | None
    rows: int
    covered: bool
    error: str | None = None
