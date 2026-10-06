from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_shadow_portfolios.py"
SPEC = importlib.util.spec_from_file_location("build_shadow_portfolios", SCRIPT)
portfolio = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(portfolio)


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def mode() -> dict:
    return {
        "id": "v4_future",
        "decision": "research/{as_of}/decision.json",
        "summary": "research/{as_of}/summary.json",
        "expected_status": "V4_COMPLETE",
        "execution_capture": "research/{as_of}/execution_prices.json",
        "execution_capture_start_date": "2026-10-12",
        "historical_signal_snapshot": "research/{as_of}/current_shadow_snapshot.csv",
        "historical_benchmark_capture": "live/{as_of}/benchmark_spy_capture.json",
    }


def top10() -> list[dict]:
    return [
        {"rank": rank, "ticker": f"T{rank}", "score": 100 - rank}
        for rank in range(1, 11)
    ]


def create_event(
    root: Path,
    as_of: str,
    *,
    selected_price: float,
    spy_price: float,
    missing_ticker: str | None = None,
) -> None:
    decision_hash = f"shadow-{as_of}"
    v1_hash = f"v1-{as_of}"
    research = root / "research" / as_of
    save(research / "decision.json", {
        "as_of": as_of,
        "v1_decision_hash": v1_hash,
        "decision_hash": decision_hash,
        "top10": top10(),
    })
    save(research / "summary.json", {
        "as_of": as_of,
        "status": "V4_COMPLETE",
        "v1_decision_hash": v1_hash,
    })
    quotes = []
    for rank in range(1, 11):
        ticker = f"T{rank}"
        if ticker == missing_ticker:
            continue
        quotes.append({
            "ticker": ticker,
            "price": selected_price,
            "price_field": "last_trade_price",
            "quote_timestamp": f"{as_of}T15:00:00Z",
            "selected_now": True,
            "benchmark": False,
        })
    quotes.append({
        "ticker": "SPY",
        "price": spy_price,
        "price_field": "last_trade_price",
        "quote_timestamp": f"{as_of}T15:00:00Z",
        "selected_now": False,
        "benchmark": True,
    })
    save(research / "execution_prices.json", {
        "schema_version": 1,
        "status": "SHADOW_EXECUTION_PRICE_CAPTURE_COMPLETE",
        "model_id": "v4_future",
        "as_of": as_of,
        "decision_hash": decision_hash,
        "captured_at": f"{as_of}T15:00:02Z",
        "broker_order_capability": False,
        "quotes": quotes,
    })


def create_historical_pricing(
    root: Path,
    as_of: str,
    *,
    stock_price: float,
    spy_price: float,
) -> None:
    snapshot = root / "research" / as_of / "current_shadow_snapshot.csv"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        "ticker,close,price_valid,price_timestamp,price_source",
    ]
    for rank in range(1, 11):
        rows.append(
            f"T{rank},{stock_price},True,{as_of}T14:00:00Z,robinhood"
        )
    snapshot.write_text("\n".join(rows) + "\n", encoding="utf-8")

    save(root / "live" / as_of / "benchmark_spy_capture.json", {
        "price": spy_price,
        "price_field": "last_trade_price",
        "quote_timestamp": f"{as_of}T15:30:00Z",
        "source": "robinhood_trading_mcp",
    })


def runs(*dates: str) -> dict[str, dict]:
    return {
        as_of: {
            "decision_hash": f"v1-{as_of}",
            "deployed_contribution": 10.0,
        }
        for as_of in dates
    }


def test_matched_cash_flow_portfolio_and_spy_accounting(tmp_path: Path) -> None:
    create_event(tmp_path, "2026-10-12", selected_price=10.0, spy_price=100.0)
    create_event(tmp_path, "2026-10-19", selected_price=11.0, spy_price=105.0)

    history, transactions, positions, result = portfolio.build_model_portfolio(
        tmp_path, mode(), runs("2026-10-12", "2026-10-19")
    )

    assert len(history) == 2
    assert len(transactions) == 20
    assert len(positions) == 10

    first = history[0]
    assert first["cumulative_contributed"] == pytest.approx(10.0)
    assert first["post_contribution_value"] == pytest.approx(10.0)
    assert first["deployed_capital_return"] == pytest.approx(0.0)

    second = history[1]
    assert second["pre_contribution_value"] == pytest.approx(11.0)
    assert second["post_contribution_value"] == pytest.approx(21.0)
    assert second["cumulative_contributed"] == pytest.approx(20.0)
    assert second["deployed_capital_return"] == pytest.approx(0.05)
    assert second["spy_post_contribution_value"] == pytest.approx(20.5)
    assert second["spy_deployed_capital_return"] == pytest.approx(0.025)
    assert second["excess_return_vs_spy"] == pytest.approx(0.025)

    summary = result["summary"]
    assert summary["portfolio_status"] == "active"
    assert summary["contribution_events"] == 2
    assert summary["actual_broker_performance"] is False
    assert summary["sell_policy"] == "no_discretionary_selling"


def test_historical_signal_prices_backfill_pre_capture_start(tmp_path: Path) -> None:
    for as_of, stock_price, spy_price in (
        ("2026-09-25", 10.0, 100.0),
        ("2026-09-28", 11.0, 102.0),
    ):
        research = tmp_path / "research" / as_of
        save(research / "decision.json", {
            "as_of": as_of,
            "v1_decision_hash": f"v1-{as_of}",
            "decision_hash": f"shadow-{as_of}",
            "top10": top10(),
        })
        save(research / "summary.json", {
            "as_of": as_of,
            "status": "V4_COMPLETE",
            "v1_decision_hash": f"v1-{as_of}",
        })
        create_historical_pricing(
            tmp_path,
            as_of,
            stock_price=stock_price,
            spy_price=spy_price,
        )

    history, transactions, _, result = portfolio.build_model_portfolio(
        tmp_path, mode(), runs("2026-09-25", "2026-09-28")
    )

    assert [row["as_of"] for row in history] == ["2026-09-25", "2026-09-28"]
    assert all(
        row["price_policy"] == "historical_signal_snapshot"
        for row in history
    )
    assert all(
        row["benchmark_policy"] == "historical_v1_benchmark_capture"
        for row in history
    )
    assert all(
        row["price_policy"] == "historical_signal_snapshot"
        for row in transactions
    )
    assert result["summary"]["historical_signal_price_events"] == 2
    assert result["summary"]["prospective_execution_capture_events"] == 0
    assert result["excluded"] == []


def test_missing_prospective_capture_is_not_backfilled(tmp_path: Path) -> None:
    # On/after 10/12 a missing execution capture must stay missing even if a
    # historical-style signal snapshot is available.
    research = tmp_path / "research" / "2026-10-12"
    save(research / "decision.json", {
        "as_of": "2026-10-12",
        "v1_decision_hash": "v1-2026-10-12",
        "decision_hash": "shadow-2026-10-12",
        "top10": top10(),
    })
    save(research / "summary.json", {
        "as_of": "2026-10-12",
        "status": "V4_COMPLETE",
        "v1_decision_hash": "v1-2026-10-12",
    })
    create_historical_pricing(
        tmp_path,
        "2026-10-12",
        stock_price=10.0,
        spy_price=100.0,
    )

    create_event(tmp_path, "2026-10-19", selected_price=10.0, spy_price=100.0)

    history, _, _, result = portfolio.build_model_portfolio(
        tmp_path, mode(), runs("2026-10-12", "2026-10-19")
    )

    assert [row["as_of"] for row in history] == ["2026-10-19"]
    assert result["excluded"] == [{
        "as_of": "2026-10-12",
        "reason": "missing_prospective_execution_capture",
    }]


def test_missing_quote_for_existing_holding_fails_closed(tmp_path: Path) -> None:
    create_event(tmp_path, "2026-10-12", selected_price=10.0, spy_price=100.0)
    create_event(
        tmp_path,
        "2026-10-19",
        selected_price=11.0,
        spy_price=105.0,
        missing_ticker="T1",
    )

    with pytest.raises(ValueError, match="cannot mark existing holdings"):
        portfolio.build_model_portfolio(
            tmp_path, mode(), runs("2026-10-12", "2026-10-19")
        )


def test_capture_script_is_read_only_and_includes_history_contract() -> None:
    root = Path(__file__).parents[1]
    source = (
        root / "scripts" / "capture_shadow_execution_prices.py"
    ).read_text(encoding="utf-8")

    assert "get_quotes" in source
    assert "place_equity_order" not in source
    assert "review_equity_order" not in source
    assert "broker_order_capability" in source
    assert "--history-glob" in source
    assert 'tickers.add("SPY")' in source
