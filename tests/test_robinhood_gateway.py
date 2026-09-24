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



def _agentic_account() -> dict:
    return {
        "account_number": "ACC3436",
        "rhs_account_number": "RHS3436",
        "brokerage_account_type": "agentic",
        "nickname": "Agentic",
        "agentic_allowed": True,
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }


def test_account_snapshot_consumes_all_position_and_order_pages():
    def positions(arguments):
        cursor = arguments.get("cursor")
        if cursor is None:
            return {
                "positions": [
                    {"symbol": "AAA", "quantity": "1"},
                ],
                "next": "positions-2",
            }
        assert cursor == "positions-2"
        return {
            "positions": [
                {"symbol": "BBB", "quantity": "2"},
            ],
            "next": "",
        }

    def orders(arguments):
        cursor = arguments.get("cursor")
        if cursor is None:
            return {
                "orders": [
                    {"id": "o1", "symbol": "AAA", "state": "filled"},
                ],
                "next": "orders-2",
            }
        assert cursor == "orders-2"
        return {
            "orders": [
                {"id": "o2", "symbol": "BBB", "state": "queued"},
            ],
            "next": "",
        }

    client = FakeClient(
        {
            "get_accounts": {"accounts": [_agentic_account()]},
            "get_portfolio": {},
            "get_equity_positions": positions,
            "get_equity_orders": orders,
            "get_equity_quotes": {
                "results": [
                    {
                        "quote": {
                            "symbol": "AAA",
                            "last_trade_price": "10.00",
                            "venue_last_trade_time": "2026-09-24T14:00:00Z",
                        }
                    },
                    {
                        "quote": {
                            "symbol": "BBB",
                            "last_trade_price": "20.00",
                            "venue_last_trade_time": "2026-09-24T14:00:00Z",
                        }
                    },
                ]
            },
        }
    )

    snapshot = asyncio.run(RobinhoodBrokerGateway(client=client).get_account_snapshot())

    assert snapshot["derived_position_valuations"] == [
        {
            "symbol": "AAA",
            "derived_last_price": 10.0,
            "price_timestamp": "2026-09-24T14:00:00Z",
            "price_field": "last_trade_price",
            "derived_market_value": 10.0,
        },
        {
            "symbol": "BBB",
            "derived_last_price": 20.0,
            "price_timestamp": "2026-09-24T14:00:00Z",
            "price_field": "last_trade_price",
            "derived_market_value": 40.0,
        },
    ]
    assert [row["id"] for row in snapshot["non_final_equity_orders"]] == ["o2"]

    position_provenance = snapshot["raw_responses"]["positions"]["response"][
        "pagination_provenance"
    ]
    order_provenance = snapshot["raw_responses"]["orders"]["response"][
        "pagination_provenance"
    ]
    assert position_provenance["page_count"] == 2
    assert position_provenance["row_count"] == 2
    assert len(position_provenance["pages"]) == 2
    assert order_provenance["page_count"] == 2
    assert order_provenance["row_count"] == 2

    assert ("get_equity_positions", {"account_number": "ACC3436"}) in client.calls
    assert (
        "get_equity_positions",
        {"account_number": "ACC3436", "cursor": "positions-2"},
    ) in client.calls
    assert ("get_equity_orders", {"account_number": "ACC3436"}) in client.calls
    assert (
        "get_equity_orders",
        {"account_number": "ACC3436", "cursor": "orders-2"},
    ) in client.calls


def test_paginated_collection_repeated_cursor_fails_closed():
    def orders(arguments):
        order_id = "o1" if arguments.get("cursor") is None else "o2"
        return {
            "orders": [{"id": order_id, "symbol": "AAA", "state": "queued"}],
            "next": "same-cursor",
        }

    client = FakeClient({"get_equity_orders": orders})
    gateway = RobinhoodBrokerGateway(client=client)

    with pytest.raises(RobinhoodMCPError, match="repeated cursor"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_orders",
                base_arguments={"account_number": "ACC3436"},
                collection_key="orders",
                identity_key="id",
                resource="orders",
            )
        )


def test_paginated_collection_duplicate_identity_fails_closed():
    def orders(arguments):
        if arguments.get("cursor") is None:
            return {
                "orders": [{"id": "o1", "symbol": "AAA", "state": "queued"}],
                "next": "page-2",
            }
        return {
            "orders": [{"id": "o1", "symbol": "AAA", "state": "filled"}],
            "next": "",
        }

    gateway = RobinhoodBrokerGateway(client=FakeClient({"get_equity_orders": orders}))

    with pytest.raises(RobinhoodMCPError, match="duplicate id='o1'"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_orders",
                base_arguments={"account_number": "ACC3436"},
                collection_key="orders",
                identity_key="id",
                resource="orders",
            )
        )


def test_paginated_collection_malformed_cursor_fails_closed():
    gateway = RobinhoodBrokerGateway(
        client=FakeClient(
            {
                "get_equity_positions": {
                    "positions": [{"symbol": "AAA", "quantity": "1"}],
                    "next": 123,
                }
            }
        )
    )

    with pytest.raises(RobinhoodMCPError, match="malformed next cursor"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_positions",
                base_arguments={"account_number": "ACC3436"},
                collection_key="positions",
                identity_key="symbol",
                resource="positions",
            )
        )


def test_paginated_collection_page_limit_fails_closed():
    counter = {"value": 0}

    def orders(arguments):
        counter["value"] += 1
        return {
            "orders": [
                {
                    "id": f"o{counter['value']}",
                    "symbol": "AAA",
                    "state": "queued",
                }
            ],
            "next": f"cursor-{counter['value']}",
        }

    gateway = RobinhoodBrokerGateway(client=FakeClient({"get_equity_orders": orders}))

    with pytest.raises(RobinhoodMCPError, match="exceeded page limit"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_orders",
                base_arguments={"account_number": "ACC3436"},
                collection_key="orders",
                identity_key="id",
                resource="orders",
                max_pages=2,
            )
        )


def test_paginated_collection_row_limit_fails_closed():
    gateway = RobinhoodBrokerGateway(
        client=FakeClient(
            {
                "get_equity_positions": {
                    "positions": [
                        {"symbol": "AAA", "quantity": "1"},
                        {"symbol": "BBB", "quantity": "1"},
                    ],
                    "next": "",
                }
            }
        )
    )

    with pytest.raises(RobinhoodMCPError, match="exceeded row limit"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_positions",
                base_arguments={"account_number": "ACC3436"},
                collection_key="positions",
                identity_key="symbol",
                resource="positions",
                max_rows=1,
            )
        )


def test_paginated_collection_rejects_unsupported_pagination_marker():
    gateway = RobinhoodBrokerGateway(
        client=FakeClient(
            {
                "get_equity_orders": {
                    "orders": [],
                    "next_cursor": "legacy-page-2",
                }
            }
        )
    )

    with pytest.raises(RobinhoodMCPError, match="unsupported pagination marker"):
        asyncio.run(
            gateway._get_paginated_collection(
                tool_name="get_equity_orders",
                base_arguments={"account_number": "ACC3436"},
                collection_key="orders",
                identity_key="id",
                resource="orders",
            )
        )

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
