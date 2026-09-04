from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from ..prices import DailyPrice, PriceCoverageResult


class EulerpoolClient:
    """Eulerpool historical equity-price client.

    Eulerpool's current public docs show both /v1/equities/... and
    /api/1/equities/... examples. Try the current /v1 route first and retain
    /api/1 as a compatibility fallback.
    """

    base_urls = (
        "https://api.eulerpool.com/v1/equities",
        "https://api.eulerpool.com/api/1/equities",
    )

    def __init__(self, token: str, *, timeout_seconds: int = 30) -> None:
        if not token.strip():
            raise ValueError("Eulerpool API token is required")
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
                "from": start.isoformat(),
                "to": end.isoformat(),
                "interval": "1d",
            }
        )
        symbol = urllib.parse.quote(ticker.upper())

        last_error: Exception | None = None
        payload = None

        for base_url in self.base_urls:
            url = f"{base_url}/{symbol}/history?{query}"
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                    "User-Agent": "finance-research/0.1",
                },
            )

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    continue
                raise

        if payload is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Eulerpool returned no response")

        bars = _extract_bars(payload)
        prices: list[DailyPrice] = []

        for row in bars:
            date_text = str(row.get("date") or row.get("datetime") or "")[:10]
            close = row.get("close")
            if not date_text or close is None:
                continue

            price_date = date.fromisoformat(date_text)
            if price_date < start or price_date > end:
                continue

            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
                    open=float(row.get("open", close)),
                    high=float(row.get("high", close)),
                    low=float(row.get("low", close)),
                    close=float(close),
                    volume=_parse_volume(row.get("volume")),
                    adjusted_close=_parse_float(
                        row.get("adjustedClose")
                        if row.get("adjustedClose") is not None
                        else row.get("adjusted_close")
                    ),
                    source="eulerpool",
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


def _extract_bars(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Eulerpool response: {str(payload)[:300]}")

    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    if payload.get("detail"):
        raise RuntimeError(str(payload["detail"]))
    if payload.get("message") and not any(
        key in payload for key in ("data", "bars", "results", "result")
    ):
        raise RuntimeError(str(payload["message"]))

    for key in ("data", "bars", "results", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            for nested_key in ("data", "bars", "results", "items"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]

    return []


def _parse_volume(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
