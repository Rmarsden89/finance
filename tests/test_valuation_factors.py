from __future__ import annotations

import math

import pandas as pd

from finance.factors.valuation import add_valuation_factors
from finance.factors.validation import validate_raw_factors


def test_valuation_uses_raw_close_and_annual_denominators() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2025-06-06",
        "close": 100.0,
        "adjusted_close": 50.0,
        "shares_outstanding": 10_000_000.0,
        "annual_net_income": 100_000_000.0,
        "annual_revenue": 2_000_000_000.0,
        "annual_operating_cash_flow": 180_000_000.0,
        "annual_capital_expenditures": 80_000_000.0,
        "shareholders_equity": 500_000_000.0,
        "annual_net_income_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_revenue_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_operating_cash_flow_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_capital_expenditures_accepted_at": "2025-02-15T12:00:00+00:00",
    }])

    result = add_valuation_factors(frame)
    result = validate_raw_factors(result)

    assert result.loc[0, "market_cap"] == 1_000_000_000.0
    assert math.isclose(result.loc[0, "earnings_yield_annual"], 0.10)
    assert math.isclose(result.loc[0, "sales_yield_annual"], 2.0)
    assert math.isclose(result.loc[0, "free_cash_flow_yield_annual"], 0.10)
    assert math.isclose(result.loc[0, "book_to_market"], 0.50)

    assert result.loc[0, "earnings_yield_annual_valid"]
    assert result.loc[0, "sales_yield_annual_valid"]
    assert result.loc[0, "free_cash_flow_yield_annual_valid"]
    assert result.loc[0, "book_to_market_valid"]


def test_valuation_rejects_stale_annual_fundamentals() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2025-12-31",
        "close": 100.0,
        "shares_outstanding": 10_000_000.0,
        "annual_net_income": 100_000_000.0,
        "annual_revenue": 2_000_000_000.0,
        "annual_operating_cash_flow": 180_000_000.0,
        "annual_capital_expenditures": 80_000_000.0,
        "shareholders_equity": 500_000_000.0,
        "annual_net_income_accepted_at": "2024-01-01T12:00:00+00:00",
        "annual_revenue_accepted_at": "2024-01-01T12:00:00+00:00",
        "annual_operating_cash_flow_accepted_at": "2024-01-01T12:00:00+00:00",
        "annual_capital_expenditures_accepted_at": "2024-01-01T12:00:00+00:00",
    }])

    result = validate_raw_factors(add_valuation_factors(frame))

    assert not result.loc[0, "earnings_yield_annual_valid"]
    assert pd.isna(result.loc[0, "earnings_yield_annual_validated"])
    assert "stale_annual_fact" in result.loc[0, "earnings_yield_annual_invalid_reason"]


def test_negative_earnings_and_fcf_remain_valid_valuation_signals() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2025-06-06",
        "close": 20.0,
        "shares_outstanding": 10_000_000.0,
        "annual_net_income": -25_000_000.0,
        "annual_revenue": 500_000_000.0,
        "annual_operating_cash_flow": 10_000_000.0,
        "annual_capital_expenditures": 30_000_000.0,
        "shareholders_equity": 100_000_000.0,
        "annual_net_income_accepted_at": "2025-03-01T12:00:00+00:00",
        "annual_revenue_accepted_at": "2025-03-01T12:00:00+00:00",
        "annual_operating_cash_flow_accepted_at": "2025-03-01T12:00:00+00:00",
        "annual_capital_expenditures_accepted_at": "2025-03-01T12:00:00+00:00",
    }])

    result = validate_raw_factors(add_valuation_factors(frame))

    assert result.loc[0, "earnings_yield_annual"] < 0
    assert result.loc[0, "free_cash_flow_yield_annual"] < 0
    assert result.loc[0, "earnings_yield_annual_valid"]
    assert result.loc[0, "free_cash_flow_yield_annual_valid"]


def test_same_day_filing_before_decision_time_is_pit_valid() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2025-02-13",
        "as_of": "2025-02-13T16:00:00",
        "close": 100.0,
        "shares_outstanding": 100_000_000.0,
        "annual_net_income": 500_000_000.0,
        "annual_revenue": 5_000_000_000.0,
        "annual_operating_cash_flow": 700_000_000.0,
        "annual_capital_expenditures": 200_000_000.0,
        "shareholders_equity": 3_000_000_000.0,
        "total_assets": 8_000_000_000.0,
        "annual_net_income_accepted_at": "2025-02-13T08:52:00+00:00",
        "annual_revenue_accepted_at": "2025-02-13T08:52:00+00:00",
        "annual_operating_cash_flow_accepted_at": "2025-02-13T08:52:00+00:00",
        "annual_capital_expenditures_accepted_at": "2025-02-13T08:52:00+00:00",
    }])

    result = validate_raw_factors(add_valuation_factors(frame))

    assert result.loc[0, "earnings_yield_annual_valid"]
    assert "accepted_after_decision" not in result.loc[
        0,
        "earnings_yield_annual_invalid_reason",
    ]


def test_implausible_market_cap_scale_is_rejected() -> None:
    frame = pd.DataFrame([{
        "decision_date": "2025-06-06",
        "as_of": "2025-06-06T16:00:00",
        "close": 10.0,
        "shares_outstanding": 100.0,
        "annual_net_income": 500_000_000.0,
        "annual_revenue": 10_000_000_000.0,
        "annual_operating_cash_flow": 900_000_000.0,
        "annual_capital_expenditures": 300_000_000.0,
        "shareholders_equity": 5_000_000_000.0,
        "total_assets": 20_000_000_000.0,
        "annual_net_income_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_revenue_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_operating_cash_flow_accepted_at": "2025-02-15T12:00:00+00:00",
        "annual_capital_expenditures_accepted_at": "2025-02-15T12:00:00+00:00",
    }])

    result = validate_raw_factors(add_valuation_factors(frame))

    for factor in (
        "earnings_yield_annual",
        "sales_yield_annual",
        "free_cash_flow_yield_annual",
        "book_to_market",
    ):
        assert not result.loc[0, f"{factor}_valid"]
        assert pd.isna(result.loc[0, f"{factor}_validated"])
        assert "market_cap_scale_mismatch" in result.loc[
            0,
            f"{factor}_invalid_reason",
        ]
