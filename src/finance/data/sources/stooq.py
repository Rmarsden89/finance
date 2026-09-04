from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from datetime import date

from ..prices import DailyPrice, PriceCoverageResult


class StooqClient:
    """Minimal Stooq daily-price client for coverage research.

    Stooq's US ticker convention is SYMBOL.US. The browser download link uses
    the simple unauthenticated query with symbol plus daily interval. We
    download full history and apply the requested date window locally.
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
        query = urllib.parse.urlencode({"s": symbol, "i": "d"})
        url = f"{self.base_url}?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/152.0.0.0 Safari/537.36"
                ),
                "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://stooq.com/q/d/?s={symbol}",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")

        stripped = raw.lstrip()
        if stripped.startswith("<") or "text/html" in content_type.lower():
            preview = " ".join(stripped[:300].split())
            raise RuntimeError(
                "Stooq returned HTML instead of CSV"
                + (f": {preview}" if preview else "")
            )

        if "exceeded" in raw.lower() or "error" in raw.lower():
            raise RuntimeError(raw.strip()[:300])

        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames or "Date" not in reader.fieldnames:
            raise RuntimeError(f"Unexpected Stooq response: {raw[:200]!r}")

        prices: list[DailyPrice] = []
        for row in reader:
            if not row.get("Date") or not row.get("Close"):
                continue

            price_date = date.fromisoformat(row["Date"])
            if price_date < start or price_date > end:
                continue

            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
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
