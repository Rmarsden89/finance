from __future__ import annotations

import json

import pandas as pd
import pytest

from finance.evaluation.package import EvaluationPackageError, build_v1_evaluation_package


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _complete_run(tmp_path, *, with_benchmark: bool = True):
    run = tmp_path / "2026-09-21"
    run.mkdir()
    _write_json(run / "workflow_state.json", {"status": "COMPLETE", "run_id": "long_growth_v1-2026-09-21"})
    _write_json(run / "shadow_decision.json", {"model_id": "long_growth_v1", "decision_hash": "abc123", "planned_investment": 10.0, "decisions": [{"ticker": "AAA", "rank": 1, "score": 80.0, "status": "buy", "allocation_dollars": 10.0}]})
    _write_json(run / "post_fill_reconciliation.json", {"portfolio_state_ready": True, "reconciled": True, "cash_change": -10.0})
    _write_json(run / "submission_reconciliation_postfill.json", {"matches": [{"ticker": "AAA", "broker_order_id": "order-1", "filled_quantity": 0.5, "average_price": 20.0, "submitted_at": "2026-09-21T14:50:00Z"}]})
    pd.DataFrame([{"ticker": "AAA", "market_value": 10.25}]).to_csv(run / "portfolio_state.csv", index=False)
    if with_benchmark:
        _write_json(run / "benchmark_spy_capture.json", {"symbol": "SPY", "price": 700.0, "quote_timestamp": "2026-09-21T14:49:59Z", "captured_at": "2026-09-21T14:50:00Z", "price_field": "last_trade_price", "source": "robinhood_trading_mcp"})
    return run


def test_build_evaluation_package_with_intraday_spy_capture(tmp_path):
    run = _complete_run(tmp_path)
    pkg = build_v1_evaluation_package(run)
    assert pkg["evaluation_only"] is True
    assert pkg["broker_order_capability"] is False
    assert pkg["deployed_contribution"] == pytest.approx(10.0)
    assert pkg["postfill_position_value"] == pytest.approx(10.25)
    assert pkg["selection_count"] == 1
    assert pkg["benchmark"]["status"] == "captured"
    assert pkg["benchmark"]["capture"]["price"] == pytest.approx(700.0)
    assert pkg["selections"][0]["average_price"] == pytest.approx(20.0)
    assert "benchmark_spy_capture" in pkg["artifacts"]


def test_historical_run_without_spy_capture_is_packaged_as_missing(tmp_path):
    run = _complete_run(tmp_path, with_benchmark=False)
    pkg = build_v1_evaluation_package(run)
    assert pkg["benchmark"]["status"] == "missing_historical_capture"
    assert pkg["benchmark"]["capture"] is None


def test_package_fails_closed_for_non_complete_run(tmp_path):
    run = _complete_run(tmp_path)
    _write_json(run / "workflow_state.json", {"status": "POSTFILL_PENDING"})
    with pytest.raises(EvaluationPackageError, match="not COMPLETE"):
        build_v1_evaluation_package(run)
