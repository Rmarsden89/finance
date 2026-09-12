from finance.shadow.robinhood_state import normalize_robinhood_shadow_state


def test_normalize_robinhood_shadow_state() -> None:
    payload = {
        "raw_responses": {
            "portfolio": {"response": {"structuredContent": {"data": {
                "total_value": "100", "equity_value": "90", "cash": "10",
                "buying_power": {"buying_power": "10"}
            }}}},
            "positions": {"response": {"structuredContent": {"data": {"positions": [
                {"symbol": "AAA", "quantity": "2", "average_buy_price": "4"}
            ]}}}},
            "orders": {"response": {"structuredContent": {"data": {"orders": []}}}},
            "tradability": {"response": {"structuredContent": {"data": {"results": [
                {"symbol": "AAA", "tradeable": True, "state": "active",
                 "fractional_tradability": "tradable",
                 "account_type_tradabilities": [
                     {"account_type": "individual", "account_type_tradability": "tradable"}
                 ]}
            ]}}}},
        },
        "derived_position_valuations": [
            {"symbol": "AAA", "derived_market_value": "10", "derived_last_price": "5",
             "price_timestamp": "2026-09-09T20:00:00Z", "instrument_id": "x"}
        ],
        "non_final_equity_orders": [],
    }

    positions, orders, audit = normalize_robinhood_shadow_state(payload)
    assert positions.loc[0, "market_value"] == 10.0
    assert len(orders) == 0
    assert audit.buying_power == 10.0
    assert audit.top10_tradable == 1


def test_empty_agentic_portfolio_is_supported() -> None:
    payload = {
        "raw_responses": {
            "portfolio": {"response": {"structuredContent": {"data": {
                "total_value": "100", "equity_value": "0", "cash": "100",
                "buying_power": {"buying_power": "100"}
            }}}},
            "positions": {"response": {"structuredContent": {"data": {"positions": []}}}},
            "orders": {"response": {"structuredContent": {"data": {"orders": []}}}},
            "tradability": {"response": {"structuredContent": {"data": {"results": []}}}},
        },
        "non_final_equity_orders": [],
    }

    positions, orders, audit = normalize_robinhood_shadow_state(payload)
    assert positions.empty
    assert orders.empty
    assert audit.account_value == 100.0
    assert audit.buying_power == 100.0


def test_direct_mcp_snake_case_envelope_is_supported() -> None:
    payload = {
        "raw_responses": {
            "portfolio": {"response": {"structured_content": {"data": {
                "total_value": "100", "equity_value": "0", "cash": "100",
                "buying_power": {"buying_power": "100"}
            }}}},
            "positions": {"response": {"structured_content": {"data": {"positions": []}}}},
            "orders": {"response": {"structured_content": {"data": {"orders": []}}}},
            "tradability": {"response": {"structured_content": {"data": {"results": [
                {"symbol": "AAA", "tradeable": True, "state": "active",
                 "fractional_tradability": "tradable",
                 "account_type_tradabilities": [
                     {"account_type": "individual", "account_type_tradability": "tradable"}
                 ]}
            ]}}}},
        },
        "non_final_equity_orders": [],
    }

    positions, orders, audit = normalize_robinhood_shadow_state(payload)
    assert positions.empty
    assert orders.empty
    assert audit.account_value == 100.0
    assert audit.buying_power == 100.0
    assert audit.top10_checked == 1
    assert audit.top10_tradable == 1
