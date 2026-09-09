import pytest

from finance.shadow.order_intent import build_order_intents


def _decision() -> dict:
    return {
        "decision_hash": "abc",
        "planned_investment": 10.0,
        "decisions": [
            {"rank": i, "ticker": f"T{i}", "status": "buy", "allocation_dollars": 1.0}
            for i in range(1, 11)
        ],
    }


def test_build_order_intents_from_ready_gate() -> None:
    batch = build_order_intents(
        _decision(),
        {"ready": True, "decision_hash": "abc"},
    )
    assert batch.order_count == 10
    assert batch.total_dollars == pytest.approx(10.0)
    assert all(row.market_hours == "regular_hours" for row in batch.intents)
    assert all(row.time_in_force == "gfd" for row in batch.intents)
    assert len({row.idempotency_key for row in batch.intents}) == 10


def test_gate_hash_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="decision_hash"):
        build_order_intents(
            _decision(),
            {"ready": True, "decision_hash": "different"},
        )


def test_gate_not_ready_fails_closed() -> None:
    with pytest.raises(ValueError, match="not READY"):
        build_order_intents(
            _decision(),
            {"ready": False, "decision_hash": "abc"},
        )
