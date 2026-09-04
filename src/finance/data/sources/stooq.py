from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from datetime import date

from ..prices import DailyPrice, PriceCoverageResult


class StooqClient:
    """Minimal Stooq daily-price client for coverage research.

    Stooq's US ticker convention is SYMBOL.US. The historical CSV endpoint is
    unauthenticated and returns raw daily OHLCV. It does not provide explicit
    split/dividend event fields, so those will need a companion source later.
    """

    base_url = "https://stooq.com/q/d/l/"

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def daily_prices(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
    ) -> list[DailyPrice]:
        symbol = self._symbol(ticker)
        query = urllib.parse.urlencode(
            {
                "s": symbol,
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
                "i": "d",
            }
        )
        url = f"{self.base_url}?{query}"

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "finance-research/0.1"},
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")

        if raw.lstrip().startswith("<"):
            raise RuntimeError("Stooq returned HTML instead of CSV")
        if "exceeded" in raw.lower() or "error" in raw.lower():
            raise RuntimeError(raw.strip()[:300])

        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames or "Date" not in reader.fieldnames:
            raise RuntimeError(f"Unexpected Stooq response: {raw[:200]!r}")

        prices: list[DailyPrice] = []
        for row in reader:
            if not row.get("Date") or not row.get("Close"):
                continue
            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=date.fromisoformat(row["Date"]),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=_parse_int(row.get("Volume")),
                    source="stooq",
                )
            )

        return prices

    def coverage(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
    ) -> PriceCoverageResult:
        try:
            rows = self.daily_prices(ticker, start=start, end=end)
        except Exception as exc:
            return PriceCoverageResult(
                ticker=ticker.upper(),
                requested_start=start,
                requested_end=end,
                first_price_date=None,
                last_price_date=None,
                rows=0,
                covered=False,
                error=str(exc),
            )

        return PriceCoverageResult(
            ticker=ticker.upper(),
            requested_start=start,
            requested_end=end,
            first_price_date=rows[0].date if rows else None,
            last_price_date=rows[-1].date if rows else None,
            rows=len(rows),
            covered=bool(rows),
        )

    @staticmethod
    def _symbol(ticker: str) -> str:
        return f"{ticker.strip().lower()}.us"


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None
