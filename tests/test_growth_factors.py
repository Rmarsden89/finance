from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from finance.factors.growth import add_growth_factors


def _weekly_rows(
    ticker: str,
    *,
    revenue_start: float,
    revenue_end: float,
    income_start: float,
    income_end: float,
) -> list[dict]:
    start = date(2024, 1, 5)
    rows = []
    for week in range(53):
        rows.append(
            {
                "ticker": ticker,
                "decision_date": start + timedelta(days=7 * week),
                "revenue": revenue_start if week < 52 else revenue_end,
                "net_income": income_start if week < 52 else income_end,
                "operating_income": income_start if week < 52 else income_end,
                "operating_cash_flow": income_start if week < 52 else income_end,
            }
        )
    return rows


def test_growth_factors_compare_roughly_one_year_pit_history() -> None:
    panel = pd.DataFrame(
        _weekly_rows(
            "AAA",
            revenue_start=100.0,
            revenue_end=120.0,
            income_start=-10.0,
            income_end=5.0,
        )
    )

    result = add_growth_factors(panel)
    last = result.iloc[-1]

    assert last["growth_lookback_valid"]
    assert last["growth_lookback_days"] == 364
    assert last["revenue_growth_1y"] == 0.2
    assert last["net_income_growth_1y"] == 1.5
    assert last["operating_income_growth_1y"] == 1.5
    assert last["operating_cash_flow_growth_1y"] == 1.5


def test_growth_factors_do_not_force_short_history() -> None:
    panel = pd.DataFrame(
        _weekly_rows(
            "AAA",
            revenue_start=100.0,
            revenue_end=120.0,
            income_start=10.0,
            income_end=12.0,
        )[:20]
    )

    result = add_growth_factors(panel)

    assert result["growth_lookback_valid"].sum() == 0
    assert result["revenue_growth_1y"].isna().all()
