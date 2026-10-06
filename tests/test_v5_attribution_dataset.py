from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from finance.backtest import BacktestPriceStore
from finance.research.v5_attribution import (
    add_v1_rank_fields,
    build_v5_attribution_dataset,
)


def write_prices(path: Path) -> None:
    rows = [
        ("AAA", "2026-01-02", 100, 100),
        ("AAA", "2026-01-09", 110, 110),
        ("BBB", "2026-01-02", 100, 100),
        ("BBB", "2026-01-09", 90, 90),
        ("CCC", "2026-01-02", 100, 100),
        ("CCC", "2026-01-09", 100, 100),
        ("SPY", "2026-01-02", 100, 100),
        ("SPY", "2026-01-09", 105, 105),
    ]
    pd.DataFrame(
        rows,
        columns=["pit_ticker", "date", "open", "close"],
    ).to_csv(path, index=False)


def scored_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"decision_date": "2026-01-02", "ticker": "BBB", "long_growth_v1_score": 80, "top_conviction_eligible": True},
            {"decision_date": "2026-01-02", "ticker": "AAA", "long_growth_v1_score": 80, "top_conviction_eligible": True},
            {"decision_date": "2026-01-02", "ticker": "CCC", "long_growth_v1_score": 70, "top_conviction_eligible": False},
        ]
    )


def test_rank_is_score_desc_then_ticker_ascending() -> None:
    ranked = add_v1_rank_fields(scored_frame())
    ranks = dict(zip(ranked["ticker"], ranked["v1_rank"]))
    assert ranks["AAA"] == 1
    assert ranks["BBB"] == 2
    assert pd.isna(ranks["CCC"])


def test_duplicate_decision_ticker_fails_closed() -> None:
    frame = pd.concat([scored_frame(), scored_frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate decision_date/ticker"):
        add_v1_rank_fields(frame)


def test_forward_returns_and_spy_relative_are_explicit(tmp_path: Path) -> None:
    prices = tmp_path / "prices.csv"
    write_prices(prices)
    store = BacktestPriceStore(prices)

    output, summary = build_v5_attribution_dataset(
        scored_frame(),
        price_store=store,
        benchmark_price_store=store,
        horizons_weeks=(1,),
    )
    aaa = output.loc[output["ticker"] == "AAA"].iloc[0]
    bbb = output.loc[output["ticker"] == "BBB"].iloc[0]

    assert aaa["fwd_1w_status"] == "mature"
    assert aaa["fwd_1w_return"] == pytest.approx(0.10)
    assert aaa["fwd_1w_spy_return"] == pytest.approx(0.05)
    assert aaa["fwd_1w_excess_return"] == pytest.approx(0.05)
    assert bbb["fwd_1w_return"] == pytest.approx(-0.10)
    assert summary.duplicate_keys == 0


def test_missing_exit_is_not_imputed(tmp_path: Path) -> None:
    prices = tmp_path / "prices.csv"
    pd.DataFrame(
        [
            ("AAA", "2026-01-02", 100, 100),
            ("SPY", "2026-01-02", 100, 100),
            ("SPY", "2026-01-09", 105, 105),
        ],
        columns=["pit_ticker", "date", "open", "close"],
    ).to_csv(prices, index=False)

    output, _ = build_v5_attribution_dataset(
        scored_frame().iloc[[0]].assign(ticker="AAA"),
        price_store=BacktestPriceStore(prices),
        benchmark_price_store=BacktestPriceStore(prices),
        horizons_weeks=(1,),
    )
    row = output.iloc[0]
    assert row["fwd_1w_status"] == "missing_exit_price"
    assert pd.isna(row["fwd_1w_return"])
