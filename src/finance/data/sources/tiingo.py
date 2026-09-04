from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date

from ..prices import DailyPrice, PriceCoverageResult


class TiingoClient:
    """Tiingo EOD client for historical market-data coverage research."""

    base_url = "https://api.tiingo.com/tiingo/daily"

    def __init__(self, token: str, *, timeout_seconds: int = 30) -> None:
        if not token.strip():
            raise ValueError("Tiingo API token is required")
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds

    def daily_prices(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
    ) -> list[DailyPrice]:
        query = urllib.parse.urlencode(
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "resampleFreq": "daily",
                "format": "json",
            }
        )
        url = f"{self.base_url}/{urllib.parse.quote(ticker.upper())}/prices?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "finance-research/0.1",
            },
        )

        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if isinstance(payload, dict) and payload.get("detail"):
            raise RuntimeError(str(payload["detail"]))
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected Tiingo response: {str(payload)[:300]}")

        prices: list[DailyPrice] = []
        for row in payload:
            date_text = str(row.get("date", ""))[:10]
            if not date_text or row.get("close") is None:
                continue
            price_date = date.fromisoformat(date_text)
            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=_parse_volume(row.get("volume")),
                    adjusted_close=(
                        float(row["adjClose"])
                        if row.get("adjClose") is not None
                        else None
                    ),
                    source="tiingo",
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


def _parse_volume(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
