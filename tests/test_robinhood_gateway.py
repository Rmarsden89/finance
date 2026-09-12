import asyncio

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        payload = self.responses[name]
        if callable(payload):
            payload = payload(arguments)
        return {
            "isError": False,
            "structuredContent": {"data": payload},
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
