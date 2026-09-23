from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
)
from finance.research.allocation_backtest import (
    AllocationBacktestConfig,
    run_allocation_backtest,
)


def _prices(path: Path) -> None:
    rows = []
    for ticker, base in (("AAA", 10.0), ("BBB", 20.0), ("CCC", 30.0)):
        for day, bump in (
            ("2020-01-03", 0.0),
            ("2020-01-06", 0.5),
            ("2020-01-10", 1.0),
            ("2020-01-13", 1.5),
            ("2020-01-17", 2.0),
            ("2020-01-20", 2.5),
        ):
            close = base + bump
            rows.append({
                "pit_ticker": ticker,
                "market_ticker": ticker,
                "date": day,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adjusted_close": close,
                "volume": 1000,
                "source": "tiingo",
            })
    pd.DataFrame(rows).sort_values(
        ["pit_ticker", "date"]
    ).to_csv(path, index=False)


def _signals() -> pd.DataFrame:
    rows = []
    for decision in ("2020-01-03", "2020-01-10", "2020-01-17"):
        rows.extend([
            {
                "decision_date": decision,
                "ticker": "AAA",
                "score": 100.0,
                "eligible": True,
            },
            {
                "decision_date": decision,
                "ticker": "BBB",
                "score": 90.0,
                "eligible": True,
            },
            {
                "decision_date": decision,
                "ticker": "CCC",
                "score": 80.0,
                "eligible": True,
            },
        ])
    return pd.DataFrame(rows)


def test_equal_dollar_reproduces_frozen_backtest_mechanics(
    tmp_path: Path,
) -> None:
    prices = tmp_path / "prices.csv"
    _prices(prices)
    store = BacktestPriceStore(prices)
    signals = _signals()

    baseline = run_ranked_accumulation_backtest(
        signals,
        price_store=store,
        model_id="baseline",
        score_column="score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=3,
            selection_flag="eligible",
            max_addon_position_weight=0.50,
        ),
        start=date(2020, 1, 1),
    )
    research = run_allocation_backtest(
        signals,
        price_store=store,
        model_id="research",
        score_column="score",
        config=AllocationBacktestConfig(
            weekly_contribution=10.0,
            top_n=3,
            selection_flag="eligible",
            max_addon_position_weight=0.50,
            rule_id="equal_dollar",
        ),
        start=date(2020, 1, 1),
    )

    for key in (
        "terminal_value",
        "xirr",
        "time_weighted_return",
        "annualized_time_weighted_return",
        "max_drawdown",
        "ending_cash",
    ):
        assert math.isclose(
            float(research.summary[key]),
            float(baseline.summary[key]),
            rel_tol=0,
            abs_tol=1e-12,
        )

    baseline_buys = baseline.trades.loc[
        baseline.trades["side"].eq("buy"), "dollars"
    ].reset_index(drop=True)
    research_buys = research.trades.loc[
        research.trades["side"].eq("buy"), "dollars"
    ].reset_index(drop=True)
    pd.testing.assert_series_equal(
        research_buys,
        baseline_buys,
        check_names=False,
    )


def test_rank_weighted_concentrates_more_than_equal_dollar(
    tmp_path: Path,
) -> None:
    prices = tmp_path / "prices.csv"
    _prices(prices)
    store = BacktestPriceStore(prices)
    signals = _signals()

    equal = run_allocation_backtest(
        signals,
        price_store=store,
        model_id="equal",
        score_column="score",
        config=AllocationBacktestConfig(
            weekly_contribution=10.0,
            top_n=3,
            selection_flag="eligible",
            max_addon_position_weight=0.90,
            rule_id="equal_dollar",
        ),
        start=date(2020, 1, 1),
    )
    ranked = run_allocation_backtest(
        signals,
        price_store=store,
        model_id="ranked",
        score_column="score",
        config=AllocationBacktestConfig(
            weekly_contribution=10.0,
            top_n=3,
            selection_flag="eligible",
            max_addon_position_weight=0.90,
            rule_id="rank_weighted",
        ),
        start=date(2020, 1, 1),
    )

    assert (
        ranked.summary["mean_contribution_hhi"]
        > equal.summary["mean_contribution_hhi"]
    )
