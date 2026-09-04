from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timezone

from ..prices import DailyPrice, PriceCoverageResult


class EulerpoolClient:
    """Eulerpool historical equity-price client.

    Current API flow:
      1. Resolve ticker/identifier through /api/1/equity/profile/{identifier}
      2. Use returned ISIN with /api/1/equity/quotes/{isin}
      3. Pass startdate/enddate as Unix milliseconds.

    Authentication is sent as the documented token query parameter.
    """

    base_url = "https://api.eulerpool.com/api/1"

    def __init__(self, api_key: str, *, timeout_seconds: int = 30) -> None:
        if not api_key.strip():
            raise ValueError("Eulerpool API key is required")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self._isin_cache: dict[str, str] = {}

    def resolve_isin(self, identifier: str) -> str:
        key = identifier.strip().upper()
        if key in self._isin_cache:
            return self._isin_cache[key]

        query = urllib.parse.urlencode({"token": self.api_key})
        url = (
            f"{self.base_url}/equity/profile/"
            f"{urllib.parse.quote(key)}?{query}"
        )

        payload = self._get_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Unexpected Eulerpool profile response: {str(payload)[:300]}"
            )

        isin = str(payload.get("isin") or "").strip()
        if not isin:
            raise RuntimeError(f"Eulerpool profile returned no ISIN for {key}")

        self._isin_cache[key] = isin
        return isin

    def daily_prices(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
    ) -> list[DailyPrice]:
        isin = self.resolve_isin(ticker)
        start_ms = _date_to_millis(start, end_of_day=False)
        end_ms = _date_to_millis(end, end_of_day=True)

        query = urllib.parse.urlencode(
            {
                "startdate": start_ms,
                "enddate": end_ms,
                "token": self.api_key,
            }
        )
        url = (
            f"{self.base_url}/equity/quotes/"
            f"{urllib.parse.quote(isin)}?{query}"
        )

        payload = self._get_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(
                f"Unexpected Eulerpool quotes response: {str(payload)[:300]}"
            )

        prices: list[DailyPrice] = []
        for row in payload:
            if not isinstance(row, dict):
                continue

            timestamp = row.get("timestamp")
            price = row.get("price")
            if timestamp is None or price is None:
                continue

            price_date = datetime.fromtimestamp(
                float(timestamp) / 1000.0,
                tz=timezone.utc,
            ).date()
            if price_date < start or price_date > end:
                continue

            close = float(price)
            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=None,
                    adjusted_close=None,
                    source="eulerpool_quotes",
                )
            )

        return sorted(prices, key=lambda item: item.date)

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

    def _get_json(self, url: str):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "finance-research/0.1",
            },
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))


def _date_to_millis(value: date, *, end_of_day: bool) -> int:
    clock = time.max if end_of_day else time.min
    dt = datetime.combine(value, clock, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
