from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from ..prices import DailyPrice, PriceCoverageResult


class YahooClient:
    """Yahoo Finance chart-endpoint client for daily market-data research.

    The v8 chart endpoint returns daily OHLCV, adjusted close, and corporate
    action events without requiring an API key. Historical delisted-symbol
    retention is not assumed; the coverage audit measures it explicitly.
    """

    base_url = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def daily_prices(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
    ) -> list[DailyPrice]:
        period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        period2 = int(datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc).timestamp())
        query = urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        url = f"{self.base_url}{urllib.parse.quote(ticker.upper())}?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/152.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            },
        )

        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        chart = payload.get("chart", {})
        if chart.get("error"):
            raise RuntimeError(str(chart["error"]))

        results = chart.get("result") or []
        if not results:
            return []

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        adjclose_rows = ((result.get("indicators") or {}).get("adjclose") or [{}])
        adjclose = adjclose_rows[0].get("adjclose", []) if adjclose_rows else []

        prices: list[DailyPrice] = []
        for index, timestamp in enumerate(timestamps):
            close = _at(quote.get("close"), index)
            if close is None:
                continue

            price_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
            if price_date < start or price_date > end:
                continue

            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
                    open=float(_at(quote.get("open"), index) or close),
                    high=float(_at(quote.get("high"), index) or close),
                    low=float(_at(quote.get("low"), index) or close),
                    close=float(close),
                    volume=_parse_volume(_at(quote.get("volume"), index)),
                    adjusted_close=(
                        float(_at(adjclose, index))
                        if _at(adjclose, index) is not None
                        else None
                    ),
                    source="yahoo",
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


def _at(values, index):
    if values is None or index >= len(values):
        return None
    return values[index]


def _parse_volume(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
