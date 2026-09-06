from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

from ..prices import DailyPrice, PriceCoverageResult


@dataclass(frozen=True)
class TwelveDataSymbol:
    symbol: str
    exchange: str | None
    name: str | None
    country: str | None
    instrument_type: str | None
    source: str


class TwelveDataClient:
    """Twelve Data client for symbol resolution and fallback price coverage."""

    api_root = "https://api.twelvedata.com"

    def __init__(self, api_key: str, *, timeout_seconds: int = 45) -> None:
        if not api_key.strip():
            raise ValueError("Twelve Data API key is required")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    def list_stocks(self, symbol: str) -> list[TwelveDataSymbol]:
        payload = self._get_json(
            "/stocks",
            {
                "symbol": symbol.upper(),
                "country": "United States",
                "format": "JSON",
            },
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [
            self._parse_symbol_row(row, source="stocks")
            for row in data
            if isinstance(row, dict)
        ]

    def symbol_search(self, symbol: str) -> list[TwelveDataSymbol]:
        payload = self._get_json(
            "/symbol_search",
            {"symbol": symbol.upper()},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [
            self._parse_symbol_row(row, source="symbol_search")
            for row in data
            if isinstance(row, dict)
        ]

    def daily_prices(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        exchange: str | None = None,
    ) -> list[DailyPrice]:
        params = {
            "symbol": ticker.upper(),
            "interval": "1day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "order": "asc",
            "format": "JSON",
            "adjust": "splits",
        }
        if exchange:
            params["exchange"] = exchange

        payload = self._get_json("/time_series", params)

        values = payload.get("values")
        if values is None:
            return []
        if not isinstance(values, list):
            raise RuntimeError(
                f"Unexpected Twelve Data values: {str(values)[:300]}"
            )

        prices: list[DailyPrice] = []
        for row in values:
            if not isinstance(row, dict):
                continue
            date_text = str(row.get("datetime", ""))[:10]
            close = _float_or_none(row.get("close"))
            if not date_text or close is None:
                continue

            price_date = date.fromisoformat(date_text)
            if price_date < start or price_date > end:
                continue

            prices.append(
                DailyPrice(
                    ticker=ticker.upper(),
                    date=price_date,
                    open=_float_or_none(row.get("open")) or close,
                    high=_float_or_none(row.get("high")) or close,
                    low=_float_or_none(row.get("low")) or close,
                    close=close,
                    volume=_parse_volume(row.get("volume")),
                    adjusted_close=None,
                    source="twelve_data",
                )
            )

        prices.sort(key=lambda row: row.date)
        return prices

    def coverage(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        exchange: str | None = None,
    ) -> PriceCoverageResult:
        try:
            rows = self.daily_prices(
                ticker,
                start=start,
                end=end,
                exchange=exchange,
            )
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

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        query = dict(params)
        query["apikey"] = self.api_key
        request = urllib.request.Request(
            f"{self.api_root}{path}?{urllib.parse.urlencode(query)}",
            headers={
                "Accept": "application/json",
                "User-Agent": "finance-research/0.1",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = getattr(response, "status", None)
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Twelve Data HTTP {exc.code}: {body[:500]!r}"
            ) from exc

        text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            raise RuntimeError(
                "Twelve Data empty response "
                f"(status={status}, content_type={content_type or 'unknown'})"
            )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            snippet = text[:300].replace("\n", " ").replace("\r", " ")
            raise RuntimeError(
                "Twelve Data non-JSON response "
                f"(status={status}, content_type={content_type or 'unknown'}, "
                f"body={snippet!r})"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Unexpected Twelve Data response: {str(payload)[:500]}"
            )

        if payload.get("status") == "error" or payload.get("code"):
            code = payload.get("code")
            message = payload.get("message") or payload.get("status") or payload
            raise RuntimeError(
                f"Twelve Data API error code={code}: {message}"
            )

        return payload

    @staticmethod
    def _parse_symbol_row(
        row: dict,
        *,
        source: str,
    ) -> TwelveDataSymbol:
        return TwelveDataSymbol(
            symbol=str(row.get("symbol") or "").strip().upper(),
            exchange=(str(row.get("exchange") or "").strip() or None),
            name=(
                str(row.get("name") or row.get("instrument_name") or "").strip()
                or None
            ),
            country=(str(row.get("country") or "").strip() or None),
            instrument_type=(
                str(row.get("type") or row.get("instrument_type") or "").strip()
                or None
            ),
            source=source,
        )


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_volume(value) -> int | None:
    number = _float_or_none(value)
    return int(number) if number is not None else None
