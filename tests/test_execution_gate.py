from finance.shadow.execution_gate import evaluate_execution_gate


def _decision() -> dict:
    return {
        "decision_date": "2026-09-09",
        "decision_hash": "abc",
        "weekly_contribution": 10.0,
        "planned_investment": 10.0,
        "pre_contribution_portfolio_value": 100.0,
        "decisions": [
            {
                "ticker": "AAA",
                "status": "buy",
                "allocation_dollars": 10.0,
            }
        ],
    }


def _broker() -> dict:
    return {
        "export_metadata": {
            "account_label": "Agentic ••••3436",
            "created_at": "2026-09-09T20:57:19.123Z",
        },
        "raw_responses": {
            "portfolio": {"response": {"structuredContent": {"data": {
                "total_value": "100",
                "equity_value": "0",
                "cash": "100",
                "buying_power": {"buying_power": "100"},
            }}}},
            "positions": {"response": {"structuredContent": {"data": {
                "positions": []
            }}}},
            "orders": {"response": {"structuredContent": {"data": {
                "orders": []
            }}}},
            "tradability": {"response": {"structuredContent": {"data": {
                "results": [{
                    "symbol": "AAA",
                    "tradeable": True,
                    "state": "active",
                    "fractional_tradability": "tradable",
                    "account_type_tradabilities": [{
                        "account_type": "individual",
                        "account_type_tradability": "tradable",
                    }],
                }]
            }}}},
        },
        "non_final_equity_orders": [],
    }


def test_ready_agentic_gate() -> None:
    result = evaluate_execution_gate(_decision(), _broker())
    assert result.ready
    assert result.reasons == ()


def test_insufficient_buying_power_fails_closed() -> None:
    broker = _broker()
    broker["raw_responses"]["portfolio"]["response"]["structuredContent"]["data"][
        "buying_power"
    ]["buying_power"] = "5"
    result = evaluate_execution_gate(_decision(), broker)
    assert not result.ready
    assert "insufficient_buying_power" in result.reasons


def test_non_agentic_account_fails_closed() -> None:
    broker = _broker()
    broker["export_metadata"]["account_label"] = "individual ••••8521"
    result = evaluate_execution_gate(_decision(), broker)
    assert not result.ready
    assert "broker_account_is_not_agentic" in result.reasons
