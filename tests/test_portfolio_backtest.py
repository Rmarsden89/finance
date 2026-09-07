from __future__ import annotations

import csv
from datetime import date

import pandas as pd

from finance.backtest.portfolio import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
)


def _write_prices(path) -> None:
    rows = [
        ["AAA", "2020-01-06", 10.0, 10.0, 5.0, "tiingo"],
        ["AAA", "2020-01-10", 11.0, 11.0, 5.5, "tiingo"],
        ["AAA", "2020-01-13", 12.0, 12.0, 6.0, "tiingo"],
        ["BBB", "2020-01-06", 20.0, 20.0, "", "stooq_bulk"],
        ["BBB", "2020-01-10", 22.0, 22.0, "", "stooq_bulk"],
        ["BBB", "2020-01-13", 24.0, 24.0, "", "stooq_bulk"],
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "pit_ticker",
                "date",
                "open",
                "close",
                "adjusted_close",
                "source",
            ]
        )
        writer.writerows(rows)


def test_price_store_uses_next_open_and_adjusted_execution_basis(tmp_path) -> None:
    path = tmp_path / "prices.csv"
    _write_prices(path)
    store = BacktestPriceStore(path)

    aaa = store.next_after("AAA", date(2020, 1, 3))
    bbb = store.next_after("BBB", date(2020, 1, 3))

    assert aaa is not None
    assert aaa.date == date(2020, 1, 6)
    assert aaa.execution_price == 5.0
    assert aaa.mark_price == 5.0

    assert bbb is not None
    assert bbb.execution_price == 20.0
    assert bbb.mark_price == 20.0


def test_ranked_backtest_invests_equal_dollars_in_top_names(tmp_path) -> None:
    path = tmp_path / "prices.csv"
    _write_prices(path)
    store = BacktestPriceStore(path)

    signals = pd.DataFrame(
        [
            {
                "decision_date": "2020-01-03",
                "ticker": "AAA",
                "score": 90.0,
                "top_conviction_eligible": True,
            },
            {
                "decision_date": "2020-01-03",
                "ticker": "BBB",
                "score": 80.0,
                "top_conviction_eligible": True,
            },
            {
                "decision_date": "2020-01-10",
                "ticker": "AAA",
                "score": 95.0,
                "top_conviction_eligible": True,
            },
            {
                "decision_date": "2020-01-10",
                "ticker": "BBB",
                "score": 70.0,
                "top_conviction_eligible": True,
            },
        ]
    )

    result = run_ranked_accumulation_backtest(
        signals,
        price_store=store,
        model_id="test",
        score_column="score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=2,
        ),
        start=date(2020, 1, 1),
    )

    buys = result.trades.loc[result.trades["side"] == "buy"]
    assert len(buys) == 4
    assert set(buys["dollars"]) == {5.0}
    assert result.summary["total_contributed"] == 20.0
    assert result.summary["buy_count"] == 4


def test_ranked_backtest_forces_exit_when_ticker_leaves_universe(tmp_path) -> None:
    path = tmp_path / "prices.csv"
    _write_prices(path)
    store = BacktestPriceStore(path)

    signals = pd.DataFrame(
        [
            {
                "decision_date": "2020-01-03",
                "ticker": "AAA",
                "score": 90.0,
                "top_conviction_eligible": True,
            },
            {
                "decision_date": "2020-01-03",
                "ticker": "BBB",
                "score": 80.0,
                "top_conviction_eligible": False,
            },
            {
                "decision_date": "2020-01-10",
                "ticker": "BBB",
                "score": 80.0,
                "top_conviction_eligible": True,
            },
        ]
    )

    result = run_ranked_accumulation_backtest(
        signals,
        price_store=store,
        model_id="test",
        score_column="score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=1,
        ),
        start=date(2020, 1, 1),
    )

    forced = result.trades.loc[result.trades["side"] == "forced_exit"]
    assert len(forced) == 1
    assert forced.iloc[0]["ticker"] == "AAA"
    assert forced.iloc[0]["reason"] == "left_investable_universe"
    assert result.summary["forced_exit_count"] == 1
