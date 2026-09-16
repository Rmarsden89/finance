import asyncio

import pytest

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, RobinhoodMCPError


class FakeClient:
    def __init__(self, responses, *, snake_case=False):
        self.responses = responses
        self.calls = []
        self.snake_case = snake_case

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        payload = self.responses[name]
        if callable(payload):
            payload = payload(arguments)
        structured_key = "structured_content" if self.snake_case else "structuredContent"
        return {
            "is_error" if self.snake_case else "isError": False,
            structured_key: {"data": payload},
            "content": [],
        }


def test_select_latest_price_prefers_newer_non_regular_trade():
    quote = {
        "last_trade_price": "100.00",
        "venue_last_trade_time": "2026-09-11T20:00:00Z",
        "last_non_reg_trade_price": "101.50",
        "venue_last_non_reg_trade_time": "2026-09-11T21:00:00Z",
    }
    price, timestamp, field = RobinhoodBrokerGateway.select_latest_price(quote)
    assert price == 101.5
    assert timestamp == "2026-09-11T21:00:00Z"
    assert field == "last_non_reg_trade_price"


def test_market_snapshot_preserves_missing_symbols():
    client = FakeClient(
        {
            "get_equity_quotes": {
                "results": [
                    {
                        "quote": {
                            "symbol": "AAA",
                            "last_trade_price": "10.00",
                            "venue_last_trade_time": "2026-09-11T19:59:00Z",
                            "last_non_reg_trade_price": None,
                            "venue_last_non_reg_trade_time": None,
                            "bid_price": "9.99",
                            "venue_bid_time": "2026-09-11T19:59:00Z",
                            "ask_price": "10.01",
                            "venue_ask_time": "2026-09-11T19:59:00Z",
                            "has_traded": True,
                            "state": "active",
                        }
                    }
                ]
            }
        }
    )
    gateway = RobinhoodBrokerGateway(client=client)
    snapshot = asyncio.run(gateway.get_market_snapshot(["AAA", "BBB"]))
    assert snapshot["export_metadata"]["universe_record_count"] == 2
    assert snapshot["export_metadata"]["quote_record_count"] == 1
    by_symbol = {row["symbol"]: row for row in snapshot["records"]}
    assert by_symbol["AAA"]["instrument_status"] == "exact_symbol_match"
    assert by_symbol["BBB"]["instrument_status"] == "not_resolved"


def test_gateway_accepts_snake_case_mcp_envelope():
    client = FakeClient(
        {
            "get_equity_quotes": {
                "results": [
                    {"quote": {"symbol": "AAA", "last_trade_price": "10.00", "venue_last_trade_time": "2026-09-11T19:59:00Z"}}
                ]
            }
        },
        snake_case=True,
    )
    snapshot = asyncio.run(RobinhoodBrokerGateway(client=client).get_market_snapshot(["AAA"]))
    assert snapshot["export_metadata"]["quote_record_count"] == 1


def test_exact_order_lookup_uses_order_id_and_returns_canonical_row():
    client = FakeClient(
        {
            "get_equity_orders": lambda arguments: {
                "orders": [{"id": arguments["order_id"], "symbol": "AAA", "state": "filled"}]
            }
        }
    )
    gateway = RobinhoodBrokerGateway(client=client)
    order = asyncio.run(
        gateway.get_equity_order_by_id(account_number="ACC3436", order_id="ORDER1")
    )
    assert order == {
        "id": "ORDER1",
        "order_id": "ORDER1",
        "symbol": "AAA",
        "ticker": "AAA",
        "state": "filled",
    }
    assert client.calls == [
        ("get_equity_orders", {"account_number": "ACC3436", "order_id": "ORDER1"})
    ]


def test_place_equity_order_returns_canonical_nested_order():
    client = FakeClient(
        {
            "place_equity_order": {
                "order": {
                    "id": "ORDER1",
                    "symbol": "AAA",
                    "state": "queued",
                    "side": "buy",
                    "dollar_based_amount": {"amount": "1.00"},
                }
            }
        }
    )
    gateway = RobinhoodBrokerGateway(client=client)
    placed = asyncio.run(
        gateway.place_equity_order(
            account_number="ACC3436",
            intent={
                "ticker": "AAA",
                "amount_dollars": 1.0,
                "idempotency_key": "11111111-1111-1111-1111-111111111111",
            },
        )
    )
    assert placed["order"]["order_id"] == "ORDER1"
    assert placed["order"]["ticker"] == "AAA"
    assert placed["order"]["state"] == "queued"


def test_account_snapshot_fails_closed_when_orders_are_paginated():
    client = FakeClient(
        {
            "get_accounts": {
                "accounts": [
                    {
                        "account_number": "ACC3436",
                        "rhs_account_number": "RHS3436",
                        "brokerage_account_type": "agentic",
                        "nickname": "Agentic",
                        "agentic_allowed": True,
                        "state": "active",
                        "deactivated": False,
                        "permanently_deactivated": False,
                    }
                ]
            },
            "get_portfolio": {},
            "get_equity_positions": {"positions": []},
            "get_equity_orders": {"orders": [], "next_cursor": "page-2"},
        }
    )
    with pytest.raises(RobinhoodMCPError, match="additional pagination"):
        asyncio.run(RobinhoodBrokerGateway(client=client).get_account_snapshot())


def test_order_arguments_preserve_legacy_dollar_intent_and_idempotency():
    intent = {
        "ticker": "AAA",
        "side": "buy",
        "order_type": "market",
        "dollar_amount": "1.00",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "idempotency_key": "11111111-1111-1111-1111-111111111111",
    }
    args = RobinhoodBrokerGateway._equity_order_arguments(
        account_number="ACC1234", intent=intent, include_ref_id=True
    )
    assert args == {
        "account_number": "ACC1234",
        "symbol": "AAA",
        "side": "buy",
        "type": "market",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "dollar_amount": "1.00",
        "ref_id": "11111111-1111-1111-1111-111111111111",
    }


def test_order_arguments_map_canonical_v1_amount_dollars():
    intent = {
        "ticker": "PTC",
        "side": "buy",
        "order_type": "market",
        "amount_dollars": 1.0,
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "idempotency_key": "8cc806f854130ca90f8b524fd9e66ddeafc887854ae6fb3ae0367549e1440c01",
    }
    args = RobinhoodBrokerGateway._equity_order_arguments(
        account_number="ACC3436", intent=intent, include_ref_id=True
    )
    assert args == {
        "account_number": "ACC3436",
        "symbol": "PTC",
        "side": "buy",
        "type": "market",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "dollar_amount": "1.00",
        "ref_id": "8cc806f854130ca90f8b524fd9e66ddeafc887854ae6fb3ae0367549e1440c01",
    }
