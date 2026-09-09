import csv
from datetime import date

import pandas as pd
import pytest

from finance.shadow import (
    PortfolioPosition,
    build_shadow_decision_plan,
)


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "decision_date": "2026-09-04",
                "ticker": ticker,
                "long_growth_v1_score": 100.0 - rank,
                "top_conviction_eligible": True,
            }
            for rank, ticker in enumerate(
                [
                    "AAA",
                    "BBB",
                    "CCC",
                    "DDD",
                    "EEE",
                    "FFF",
                    "GGG",
                    "HHH",
                    "III",
                    "JJJ",
                ],
                start=1,
            )
        ]
    )


def test_empty_portfolio_allocates_equally_across_top10() -> None:
    plan = build_shadow_decision_plan(
        _signals(),
        as_of=date(2026, 9, 4),
        weekly_contribution=10.0,
    )

    assert plan.buyable_count == 10
    assert plan.blocked_count == 0
    assert plan.planned_investment == pytest.approx(10.0)
    assert all(
        row.allocation_dollars == pytest.approx(1.0)
        for row in plan.decisions
    )


def test_appreciation_only_cap_blocks_and_redistributes() -> None:
    plan = build_shadow_decision_plan(
        _signals(),
        as_of=date(2026, 9, 4),
        positions=[
            PortfolioPosition("AAA", 20.0),
            PortfolioPosition("ZZZ", 80.0),
        ],
        weekly_contribution=10.0,
        max_addon_position_weight=0.10,
    )

    aaa = next(row for row in plan.decisions if row.ticker == "AAA")
    others = [row for row in plan.decisions if row.ticker != "AAA"]

    assert aaa.pre_contribution_weight == pytest.approx(0.20)
    assert aaa.status == "blocked"
    assert aaa.reason == "position_cap"
    assert aaa.allocation_dollars == 0.0
    assert plan.buyable_count == 9
    assert all(
        row.allocation_dollars == pytest.approx(10.0 / 9.0)
        for row in others
    )


def test_position_below_cap_is_buyable_again() -> None:
    plan = build_shadow_decision_plan(
        _signals(),
        as_of=date(2026, 9, 4),
        positions=[
            PortfolioPosition("AAA", 9.0),
            PortfolioPosition("ZZZ", 91.0),
        ],
        weekly_contribution=10.0,
        max_addon_position_weight=0.10,
    )

    aaa = next(row for row in plan.decisions if row.ticker == "AAA")
    assert aaa.pre_contribution_weight == pytest.approx(0.09)
    assert aaa.status == "buy"
    assert aaa.allocation_dollars == pytest.approx(1.0)


def test_stale_signals_fail_closed() -> None:
    with pytest.raises(ValueError, match="stale"):
        build_shadow_decision_plan(
            _signals(),
            as_of=date(2026, 9, 20),
            max_signal_age_days=7,
        )


def test_decision_hash_is_deterministic() -> None:
    first = build_shadow_decision_plan(
        _signals(),
        as_of=date(2026, 9, 4),
    )
    second = build_shadow_decision_plan(
        _signals(),
        as_of=date(2026, 9, 4),
    )

    assert first.decision_hash == second.decision_hash


def test_weekly_contribution_above_v1_cap_fails_closed() -> None:
    with pytest.raises(ValueError, match="hard maximum"):
        build_shadow_decision_plan(
            _signals(),
            as_of=date(2026, 9, 4),
            weekly_contribution=10.01,
        )


def test_blank_portfolio_market_value_fails_closed(tmp_path) -> None:
    from finance.shadow import load_portfolio_positions

    path = tmp_path / "portfolio.csv"
    path.write_text("ticker,market_value\nAAA,\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing market_value"):
        load_portfolio_positions(path)
