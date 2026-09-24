from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from finance.broker.robinhood_mcp import RobinhoodMCPClient
from finance.broker.robinhood_normalize import (
    assert_collection_complete,
    extract_mcp_data,
    normalize_order_row,
)


FINAL_EQUITY_ORDER_STATES = {
    "filled",
    "cancelled",
    "canceled",
    "rejected",
    "failed",
    "voided",
    "partially_filled_rest_cancelled",
}

MAX_ROBINHOOD_COLLECTION_PAGES = 100
MAX_ROBINHOOD_COLLECTION_ROWS = 10_000


class RobinhoodMCPError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _dollar_string(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError) as exc:
        raise RobinhoodMCPError(f"Invalid dollar amount: {value!r}") from exc


@dataclass(frozen=True)
class AgenticAccount:
    account_number: str
    rhs_account_number: str
    brokerage_account_type: str
    nickname: str | None


class RobinhoodBrokerGateway:
    """Deterministic adapter over Robinhood Trading MCP."""

    def __init__(self, client: RobinhoodMCPClient | None = None) -> None:
        self.client = client or RobinhoodMCPClient()

    async def _call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = await self.client.call_tool(name, arguments)
        try:
            data = extract_mcp_data(raw)
        except ValueError as exc:
            raise RobinhoodMCPError(str(exc)) from exc
        return raw, data

    @staticmethod
    def _unexpected_pagination_markers(data: dict[str, Any]) -> list[str]:
        """Return non-canonical pagination markers that make completeness ambiguous."""
        markers: list[str] = []
        for key in ("next_url", "next_cursor", "next_page_token"):
            if data.get(key) not in (None, "", False, []):
                markers.append(key)

        pagination = data.get("pagination")
        if pagination not in (None, "", False, {}):
            if not isinstance(pagination, dict):
                markers.append("pagination")
            else:
                for key in ("next", "next_url", "next_cursor", "next_page_token"):
                    if pagination.get(key) not in (None, "", False, []):
                        markers.append(f"pagination.{key}")
                if pagination.get("has_more") is True:
                    markers.append("pagination.has_more")

        if data.get("has_more") is True:
            markers.append("has_more")
        return markers

    async def _get_paginated_collection(
        self,
        *,
        tool_name: str,
        base_arguments: dict[str, Any],
        collection_key: str,
        identity_key: str,
        resource: str,
        max_pages: int = MAX_ROBINHOOD_COLLECTION_PAGES,
        max_rows: int = MAX_ROBINHOOD_COLLECTION_ROWS,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read a complete Robinhood cursor-paginated collection or fail closed."""
        if max_pages < 1 or max_rows < 1:
            raise RobinhoodMCPError("Pagination bounds must be positive")

        rows: list[dict[str, Any]] = []
        raw_pages: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        seen_identities: set[str] = set()
        cursor: str | None = None

        for _page_number in range(1, max_pages + 1):
            arguments = dict(base_arguments)
            if cursor is not None:
                arguments["cursor"] = cursor

            raw, data = await self._call(tool_name, arguments)
            raw_pages.append(raw)

            unexpected = self._unexpected_pagination_markers(data)
            if unexpected:
                raise RobinhoodMCPError(
                    f"Robinhood {resource} response indicates additional pagination "
                    f"using unsupported marker(s): {', '.join(sorted(unexpected))}"
                )

            page_rows = data.get(collection_key)
            if page_rows is None:
                page_rows = []
            if not isinstance(page_rows, list):
                raise RobinhoodMCPError(
                    f"Robinhood {resource} response has malformed {collection_key!r}"
                )

            for row in page_rows:
                if not isinstance(row, dict):
                    raise RobinhoodMCPError(
                        f"Robinhood {resource} response contains a malformed row"
                    )
                identity = str(row.get(identity_key) or "").strip()
                if not identity:
                    raise RobinhoodMCPError(
                        f"Robinhood {resource} row is missing stable identity "
                        f"{identity_key!r}"
                    )
                if identity in seen_identities:
                    raise RobinhoodMCPError(
                        f"Robinhood {resource} pagination returned duplicate "
                        f"{identity_key}={identity!r}"
                    )
                seen_identities.add(identity)
                rows.append(row)
                if len(rows) > max_rows:
                    raise RobinhoodMCPError(
                        f"Robinhood {resource} pagination exceeded row limit "
                        f"({max_rows})"
                    )

            next_cursor = data.get("next")
            if next_cursor in (None, ""):
                merged_data = {collection_key: rows}
                if len(raw_pages) == 1:
                    merged_raw = dict(raw_pages[0])
                else:
                    merged_raw = {
                        "isError": False,
                        "structuredContent": {"data": merged_data},
                        "content": [],
                    }
                merged_raw["pagination_provenance"] = {
                    "tool": tool_name,
                    "page_count": len(raw_pages),
                    "row_count": len(rows),
                    "pages": raw_pages,
                }
                return merged_raw, merged_data

            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise RobinhoodMCPError(
                    f"Robinhood {resource} response returned malformed next cursor"
                )
            if next_cursor in seen_cursors:
                raise RobinhoodMCPError(
                    f"Robinhood {resource} pagination repeated cursor {next_cursor!r}"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        raise RobinhoodMCPError(
            f"Robinhood {resource} pagination exceeded page limit ({max_pages})"
        )

    async def get_agentic_account(self) -> tuple[AgenticAccount, dict[str, Any]]:
        raw, data = await self._call("get_accounts", {})
        accounts = data.get("accounts") or []
        allowed = [row for row in accounts if row and row.get("agentic_allowed") is True]
        if len(allowed) != 1:
            raise RobinhoodMCPError(
                f"Expected exactly one agentic-accessible account, found {len(allowed)}"
            )
        row = allowed[0]
        if row.get("state") != "active" or row.get("deactivated") or row.get("permanently_deactivated"):
            raise RobinhoodMCPError("Agentic account is not active")
        return (
            AgenticAccount(
                account_number=str(row["account_number"]),
                rhs_account_number=str(row["rhs_account_number"]),
                brokerage_account_type=str(row.get("brokerage_account_type") or ""),
                nickname=row.get("nickname"),
            ),
            raw,
        )

    async def get_account_snapshot(self, *, tradability_symbols: list[str] | None = None) -> dict[str, Any]:
        capture_started_at = _utc_now()
        account, accounts_raw = await self.get_agentic_account()

        portfolio_raw, portfolio = await self._call(
            "get_portfolio", {"account_number": account.account_number}
        )
        positions_raw, positions_data = await self._get_paginated_collection(
            tool_name="get_equity_positions",
            base_arguments={"account_number": account.account_number},
            collection_key="positions",
            identity_key="symbol",
            resource="positions",
        )
        orders_raw, orders_data = await self._get_paginated_collection(
            tool_name="get_equity_orders",
            base_arguments={"account_number": account.account_number},
            collection_key="orders",
            identity_key="id",
            resource="orders",
        )

        positions = positions_data.get("positions") or []
        orders = [
            normalize_order_row(row)
            for row in (orders_data.get("orders") or [])
            if isinstance(row, dict)
        ]
        derived_position_valuations: list[dict[str, Any]] = []
        if positions:
            symbols = [str(row["symbol"]).upper() for row in positions if row and row.get("symbol")]
            quote_rows = await self.get_quotes(symbols)
            by_symbol = {row["symbol"]: row for row in quote_rows}
            for position in positions:
                symbol = str(position.get("symbol") or "").upper()
                quote = by_symbol.get(symbol)
                if not quote:
                    continue
                latest_price, latest_timestamp, price_field = self.select_latest_price(quote)
                quantity = float(position.get("quantity") or 0)
                derived_position_valuations.append(
                    {
                        "symbol": symbol,
                        "derived_last_price": latest_price,
                        "price_timestamp": latest_timestamp,
                        "price_field": price_field,
                        "derived_market_value": (
                            quantity * latest_price if latest_price is not None else None
                        ),
                    }
                )

        tradability_raw: dict[str, Any] | None = None
        if tradability_symbols:
            rows: list[dict[str, Any]] = []
            for batch in _chunks(sorted({s.upper() for s in tradability_symbols}), 10):
                raw, data = await self._call(
                    "get_equity_tradability",
                    {"account_number": account.account_number, "symbols": batch},
                )
                if tradability_raw is None:
                    tradability_raw = raw
                rows.extend(data.get("results") or [])
            tradability_raw = {
                "isError": False,
                "structuredContent": {"data": {"results": rows}},
            }

        non_final = [
            row
            for row in orders
            if str(row.get("state") or "").lower() not in FINAL_EQUITY_ORDER_STATES
        ]

        capture_completed_at = _utc_now()
        return {
            "export_metadata": {
                "source": "robinhood_trading_mcp",
                "workflow": "long_growth_v1",
                "capture_started_at": capture_started_at,
                "capture_completed_at": capture_completed_at,
                "read_only": True,
                "account_label": f"Agentic ••••{account.account_number[-4:]}",
            },
            "raw_responses": {
                "accounts": {"response": accounts_raw},
                "portfolio": {"response": portfolio_raw},
                "positions": {"response": positions_raw},
                "orders": {"response": orders_raw},
                **({"tradability": {"response": tradability_raw}} if tradability_raw else {}),
            },
            "portfolio": portfolio,
            "derived_position_valuations": derived_position_valuations,
            "non_final_equity_orders": non_final,
        }

    async def get_equity_order_by_id(
        self, *, account_number: str, order_id: str
    ) -> dict[str, Any] | None:
        _, data = await self._call(
            "get_equity_orders",
            {"account_number": account_number, "order_id": order_id},
        )
        try:
            assert_collection_complete(data, resource="exact order lookup")
        except ValueError as exc:
            raise RobinhoodMCPError(str(exc)) from exc
        rows = data.get("orders") or data.get("results") or []
        if isinstance(rows, list):
            return normalize_order_row(rows[0]) if rows and isinstance(rows[0], dict) else None
        if isinstance(rows, dict):
            return normalize_order_row(rows)
        order = data.get("order")
        return normalize_order_row({"order": order}) if isinstance(order, dict) else None

    async def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        unique = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
        for batch in _chunks(unique, 20):
            _, data = await self._call("get_equity_quotes", {"symbols": batch})
            for entry in data.get("results") or []:
                quote = entry.get("quote") if isinstance(entry, dict) else None
                if quote:
                    results.append(quote)
        return results

    async def get_market_snapshot(self, symbols: list[str]) -> dict[str, Any]:
        capture_started_at = _utc_now()
        unique = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
        quote_rows = await self.get_quotes(unique)
        by_symbol = {str(row.get("symbol") or "").upper(): row for row in quote_rows}

        records: list[dict[str, Any]] = []
        for symbol in unique:
            quote = by_symbol.get(symbol)
            if quote is None:
                records.append(
                    {
                        "symbol": symbol,
                        "quote_status": "not_returned",
                        "instrument_status": "not_resolved",
                        "instrument_state": None,
                    }
                )
                continue
            records.append(
                {
                    "symbol": symbol,
                    "quote_status": "returned",
                    "instrument_status": "exact_symbol_match",
                    "instrument_state": quote.get("state"),
                    "last_trade_price": quote.get("last_trade_price"),
                    "venue_last_trade_time": quote.get("venue_last_trade_time"),
                    "last_non_reg_trade_price": quote.get("last_non_reg_trade_price"),
                    "venue_last_non_reg_trade_time": quote.get("venue_last_non_reg_trade_time"),
                    "bid_price": quote.get("bid_price"),
                    "venue_bid_time": quote.get("venue_bid_time"),
                    "ask_price": quote.get("ask_price"),
                    "venue_ask_time": quote.get("venue_ask_time"),
                    "has_traded": quote.get("has_traded"),
                }
            )

        capture_completed_at = _utc_now()
        return {
            "export_metadata": {
                "source": "robinhood_trading_mcp",
                "workflow": "long_growth_v1",
                "capture_started_at": capture_started_at,
                "capture_completed_at": capture_completed_at,
                "export_created_at": capture_completed_at,
                "read_only": True,
                "universe_record_count": len(unique),
                "quote_record_count": len(quote_rows),
            },
            "records": records,
        }

    async def review_equity_order(self, *, account_number: str, intent: dict[str, Any]) -> dict[str, Any]:
        args = self._equity_order_arguments(account_number=account_number, intent=intent, include_ref_id=False)
        raw, data = await self._call("review_equity_order", args)
        return {"raw": raw, "data": data}

    async def place_equity_order(self, *, account_number: str, intent: dict[str, Any]) -> dict[str, Any]:
        if not intent.get("idempotency_key"):
            raise RobinhoodMCPError("Order intent is missing idempotency_key")
        args = self._equity_order_arguments(account_number=account_number, intent=intent, include_ref_id=True)
        raw, data = await self._call("place_equity_order", args)
        order_rows = []
        if isinstance(data.get("order"), dict):
            order_rows = [normalize_order_row(data)]
        elif isinstance(data, dict):
            order_rows = [normalize_order_row(data)]
        return {"raw": raw, "data": data, "order": order_rows[0] if order_rows else None}

    @staticmethod
    def _equity_order_arguments(
        *, account_number: str, intent: dict[str, Any], include_ref_id: bool
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "account_number": account_number,
            "symbol": str(intent["ticker"]).upper(),
            "side": intent.get("side", "buy"),
            "type": intent.get("order_type", intent.get("type", "market")),
            "time_in_force": intent.get("time_in_force", "gfd"),
            "market_hours": intent.get("market_hours", "regular_hours"),
        }
        amount = intent.get("amount_dollars")
        if amount is None:
            amount = intent.get("dollar_amount")
        if amount is not None:
            args["dollar_amount"] = _dollar_string(amount)
        elif intent.get("quantity") is not None:
            args["quantity"] = str(intent["quantity"])
        else:
            raise RobinhoodMCPError(
                "Order intent requires amount_dollars/dollar_amount or quantity"
            )
        if intent.get("limit_price") is not None:
            args["limit_price"] = str(intent["limit_price"])
        if intent.get("stop_price") is not None:
            args["stop_price"] = str(intent["stop_price"])
        if include_ref_id:
            args["ref_id"] = str(intent["idempotency_key"])
        return args

    @staticmethod
    def select_latest_price(quote: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
        candidates: list[tuple[datetime, float, str, str]] = []
        for price_field, time_field in (
            ("last_trade_price", "venue_last_trade_time"),
            ("last_non_reg_trade_price", "venue_last_non_reg_trade_time"),
        ):
            raw_price = quote.get(price_field)
            raw_time = quote.get(time_field)
            if raw_price in (None, "") or raw_time in (None, ""):
                continue
            try:
                price = float(raw_price)
                timestamp = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if price > 0:
                candidates.append((timestamp, price, price_field, str(raw_time)))
        if not candidates:
            return None, None, None
        _, price, field, raw_time = max(candidates, key=lambda item: item[0])
        return price, raw_time, field


def run(coro):
    return asyncio.run(coro)
