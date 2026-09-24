from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import BacktestPriceStore
from scripts.run_issue8_allocation_robustness import (
    _rank_sensitivity_frame,
    _score_sensitivity_frame,
    _winner_attribution,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": f"T{n:02d}",
            "score": float(100 - n),
            "eligible": True,
        }
        for n in range(1, 13)
    ])


def test_score_multiplier_one_preserves_selected_scores() -> None:
    frame = _frame()
    result = _score_sensitivity_frame(
        frame,
        score_column="score",
        eligible_column="eligible",
        multiplier=1.0,
    )

    pd.testing.assert_series_equal(
        result["issue8_allocation_score"],
        frame["score"],
        check_names=False,
    )


def test_rank_swap_changes_labels_not_membership() -> None:
    frame = _frame()
    result = _rank_sensitivity_frame(
        frame,
        score_column="score",
        eligible_column="eligible",
        swap=(1, 2),
    )

    selected = result.sort_values("score", ascending=False).head(10)
    assert set(selected["ticker"]) == {
        f"T{n:02d}" for n in range(1, 11)
    }
    ranks = dict(zip(selected["ticker"], selected["issue8_allocation_rank"]))
    assert ranks["T01"] == 2
    assert ranks["T02"] == 1
    assert ranks["T03"] == 3


def test_winner_attribution_uses_realized_plus_terminal_gain(
    tmp_path: Path,
) -> None:
    prices = tmp_path / "prices.csv"
    pd.DataFrame([
        {
            "pit_ticker": "AAA",
            "market_ticker": "AAA",
            "date": "2020-01-10",
            "open": 20.0,
            "high": 20.0,
            "low": 20.0,
            "close": 20.0,
            "adjusted_close": 20.0,
            "volume": 100,
            "source": "tiingo",
        },
        {
            "pit_ticker": "BBB",
            "market_ticker": "BBB",
            "date": "2020-01-10",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "adjusted_close": 10.0,
            "volume": 100,
            "source": "tiingo",
        },
    ]).to_csv(prices, index=False)

    trades = pd.DataFrame([
        {
            "ticker": "AAA",
            "side": "buy",
            "dollars": 10.0,
            "units": 1.0,
        },
        {
            "ticker": "BBB",
            "side": "buy",
            "dollars": 10.0,
            "units": 1.0,
        },
    ])
    attribution = _winner_attribution(
        trades,
        price_store=BacktestPriceStore(prices),
        terminal_date=date(2020, 1, 10),
    )

    assert attribution.iloc[0]["ticker"] == "AAA"
    assert attribution.iloc[0]["terminal_gain_contribution"] == 10.0
