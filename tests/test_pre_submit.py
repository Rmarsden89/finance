from datetime import datetime, timezone

from finance.shadow.pre_submit import evaluate_pre_submit


def _intents() -> dict:
    return {
        "decision_hash": "abc",
        "order_count": 1,
        "total_dollars": 1.0,
        "intents": [{
            "ticker": "AAA",
            "side": "buy",
            "order_type": "market",
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
            "amount_dollars": 1.0,
            "decision_hash": "abc",
            "idempotency_key": "key1",
        }],
    }


def _broker() -> dict:
    return {
        "export_metadata": {
            "account_label": "Agentic ••••3436",
            "created_at": "2026-09-09T20:57:00Z",
        },
        "raw_responses": {
            "portfolio": {"response": {"structuredContent": {"data": {
                "buying_power": {"buying_power": "100"}
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


def test_ready_with_fresh_snapshot() -> None:
    result = evaluate_pre_submit(
        _intents(),
        _broker(),
        now=datetime(2026, 9, 9, 20, 59, tzinfo=timezone.utc),
    )
    assert result.ready


def test_stale_snapshot_fails_closed() -> None:
    result = evaluate_pre_submit(
        _intents(),
        _broker(),
        now=datetime(2026, 9, 9, 21, 10, tzinfo=timezone.utc),
    )
    assert not result.ready
    assert "broker_snapshot_stale" in result.reasons
