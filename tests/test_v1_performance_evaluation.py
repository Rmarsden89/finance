from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from finance.evaluation.v1_performance import (
    EvaluationError,
    evaluate_v1_live_performance,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_run(
    root: Path,
    run_date: str,
    *,
    decision_hash: str,
    selections: list[tuple[str, float, int]],
    portfolio_values: dict[str, float],
    deployed: float = 10.0,
    market_prices: dict[str, float] | None = None,
) -> None:
    run = root / run_date
    run.mkdir(parents=True)
    _write_json(run / "workflow_state.json", {"status": "COMPLETE"})
    _write_json(
        run / "shadow_decision.json",
        {
            "model_id": "long_growth_v1",
            "decision_hash": decision_hash,
            "planned_investment": deployed,
            "decisions": [
                {
                    "ticker": ticker,
                    "score": score,
                    "rank": rank,
                    "status": "buy",
                    "allocation_dollars": deployed / len(selections),
                }
                for ticker, score, rank in selections
            ],
        },
    )
    _write_json(
        run / "post_fill_reconciliation.json",
        {
            "portfolio_state_ready": True,
            "cash_change": -deployed,
        },
    )
    pd.DataFrame(
        [
            {"ticker": ticker, "market_value": value}
            for ticker, value in portfolio_values.items()
        ]
    ).to_csv(run / "portfolio_state.csv", index=False)
    _write_json(
        run / "submission_reconciliation_postfill.json",
        {
            "matches": [
                {
                    "ticker": ticker,
                    "average_price": 10.0 + rank,
                    "filled_quantity": 0.1,
                    "requested_dollars": deployed / len(selections),
                }
                for ticker, _, rank in selections
            ]
        },
    )
    snapshot = market_prices or {
        ticker: 10.0 + rank for ticker, _, rank in selections
    }
    pd.DataFrame(
        [
            {"ticker": ticker, "close": price, "price_valid": True}
            for ticker, price in snapshot.items()
        ]
    ).to_csv(run / "robinhood_market_snapshot_normalized.csv", index=False)


def _write_spy(path: Path, rows: list[tuple[str, float]]) -> None:
    pd.DataFrame(
        [
            {
                "ticker": "SPY",
                "date": day,
                "close": price,
                "adjusted_close": price,
            }
            for day, price in rows
        ]
    ).to_csv(path, index=False)


def test_matched_cash_flow_benchmark_and_repeated_selections(tmp_path):
    shadow = tmp_path / "reports" / "shadow"
    _write_run(
        shadow,
        "2026-09-15",
        decision_hash="hash1",
        selections=[("AAA", 80.0, 1), ("TER", 70.0, 2)],
        portfolio_values={"AAA": 5.1, "TER": 5.1},
    )
    _write_run(
        shadow,
        "2026-09-22",
        decision_hash="hash2",
        selections=[("AAA", 81.0, 1), ("APP", 72.0, 2)],
        portfolio_values={"AAA": 10.3, "TER": 5.0, "APP": 5.1},
    )
    benchmark = tmp_path / "benchmark_spy.csv"
    _write_spy(
        benchmark,
        [("2026-09-15", 100.0), ("2026-09-22", 101.0)],
    )

    result = evaluate_v1_live_performance(
        shadow_root=shadow,
        benchmark_prices_path=benchmark,
    )

    assert result.summary["completed_runs"] == 2
    assert result.summary["cumulative_contributed"] == pytest.approx(20.0)
    assert result.summary["v1_position_value"] == pytest.approx(20.4)
    assert result.summary["distinct_selected_tickers"] == 3
    assert result.summary["selection_events"] == 4
    assert result.summary["current_position_count"] == 3

    expected_spy = (10.0 / 100.0 + 10.0 / 101.0) * 101.0
    assert result.summary["benchmark_value"] == pytest.approx(expected_spy)
    assert result.summary["excess_value"] == pytest.approx(20.4 - expected_spy)

    counts = result.selection_cohorts["ticker"].value_counts().to_dict()
    assert counts == {"AAA": 2, "TER": 1, "APP": 1}


def test_forward_horizons_remain_pending_until_mature(tmp_path):
    shadow = tmp_path / "shadow"
    _write_run(
        shadow,
        "2026-09-15",
        decision_hash="hash1",
        selections=[("AAA", 80.0, 1)],
        portfolio_values={"AAA": 10.0},
    )
    _write_run(
        shadow,
        "2026-09-20",
        decision_hash="hash2",
        selections=[("AAA", 81.0, 1)],
        portfolio_values={"AAA": 20.0},
    )
    benchmark = tmp_path / "spy.csv"
    _write_spy(
        benchmark,
        [("2026-09-15", 100.0), ("2026-09-20", 101.0)],
    )

    result = evaluate_v1_live_performance(
        shadow_root=shadow,
        benchmark_prices_path=benchmark,
    )

    first = result.selection_forward_returns.loc[
        result.selection_forward_returns["selection_date"].astype(str)
        == "2026-09-15"
    ]
    assert set(first["status"]) == {"pending"}


def test_mature_forward_return_uses_first_weekly_observation_on_or_after_target(tmp_path):
    shadow = tmp_path / "shadow"
    _write_run(
        shadow,
        "2026-09-15",
        decision_hash="hash1",
        selections=[("AAA", 80.0, 1)],
        portfolio_values={"AAA": 10.0},
        market_prices={"AAA": 11.0},
    )
    _write_run(
        shadow,
        "2026-09-22",
        decision_hash="hash2",
        selections=[("AAA", 81.0, 1)],
        portfolio_values={"AAA": 20.0},
        market_prices={"AAA": 12.1},
    )
    benchmark = tmp_path / "spy.csv"
    _write_spy(
        benchmark,
        [("2026-09-15", 100.0), ("2026-09-22", 102.0)],
    )

    result = evaluate_v1_live_performance(
        shadow_root=shadow,
        benchmark_prices_path=benchmark,
    )

    row = result.selection_forward_returns.loc[
        (result.selection_forward_returns["selection_date"].astype(str) == "2026-09-15")
        & (result.selection_forward_returns["horizon"] == "1w")
    ].iloc[0]
    assert row["status"] == "complete"
    assert row["observation_date"].isoformat() == "2026-09-22"
    assert row["stock_return"] == pytest.approx(0.10)
    assert row["benchmark_return"] == pytest.approx(0.02)
    assert row["excess_return"] == pytest.approx(0.08)


def test_missing_benchmark_price_fails_closed(tmp_path):
    shadow = tmp_path / "shadow"
    _write_run(
        shadow,
        "2026-09-15",
        decision_hash="hash1",
        selections=[("AAA", 80.0, 1)],
        portfolio_values={"AAA": 10.0},
    )
    benchmark = tmp_path / "spy.csv"
    _write_spy(benchmark, [("2026-09-14", 100.0)])

    with pytest.raises(EvaluationError, match="Missing exact SPY benchmark price"):
        evaluate_v1_live_performance(
            shadow_root=shadow,
            benchmark_prices_path=benchmark,
        )


def test_non_v1_completed_run_fails_closed(tmp_path):
    shadow = tmp_path / "shadow"
    _write_run(
        shadow,
        "2026-09-15",
        decision_hash="hash1",
        selections=[("AAA", 80.0, 1)],
        portfolio_values={"AAA": 10.0},
    )
    decision_path = shadow / "2026-09-15" / "shadow_decision.json"
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    payload["model_id"] = "some_other_model"
    _write_json(decision_path, payload)

    benchmark = tmp_path / "spy.csv"
    _write_spy(benchmark, [("2026-09-15", 100.0)])

    with pytest.raises(EvaluationError, match="Unexpected model_id"):
        evaluate_v1_live_performance(
            shadow_root=shadow,
            benchmark_prices_path=benchmark,
        )
