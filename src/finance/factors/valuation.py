from __future__ import annotations

import numpy as np
import pandas as pd


def add_valuation_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add conservative PIT valuation factors using annual SEC denominators.

    Market capitalization uses raw close, not return_price/adjusted_close,
    because valuation requires the contemporaneous quoted share price.
    """

    result = panel.copy()

    close = pd.to_numeric(result.get("close"), errors="coerce")
    shares = pd.to_numeric(result.get("shares_outstanding"), errors="coerce")

    market_cap = pd.Series(np.nan, index=result.index, dtype="float64")
    valid_market_cap = (
        close.notna()
        & shares.notna()
        & (close > 0)
        & (shares > 0)
    )
    market_cap.loc[valid_market_cap] = (
        close.loc[valid_market_cap] * shares.loc[valid_market_cap]
    )
    market_cap.loc[~np.isfinite(market_cap)] = np.nan
    result["market_cap"] = market_cap

    annual_net_income = pd.to_numeric(
        result.get("annual_net_income"),
        errors="coerce",
    )
    annual_revenue = pd.to_numeric(
        result.get("annual_revenue"),
        errors="coerce",
    )
    annual_ocf = pd.to_numeric(
        result.get("annual_operating_cash_flow"),
        errors="coerce",
    )
    annual_capex = pd.to_numeric(
        result.get("annual_capital_expenditures"),
        errors="coerce",
    )
    equity = pd.to_numeric(
        result.get("shareholders_equity"),
        errors="coerce",
    )

    result["earnings_yield_annual"] = _yield_ratio(
        annual_net_income,
        market_cap,
    )
    result["sales_yield_annual"] = _yield_ratio(
        annual_revenue,
        market_cap,
    )

    annual_fcf = annual_ocf - annual_capex
    result["annual_free_cash_flow"] = annual_fcf
    result["free_cash_flow_yield_annual"] = _yield_ratio(
        annual_fcf,
        market_cap,
    )

    book = pd.Series(np.nan, index=result.index, dtype="float64")
    valid_book = equity.notna() & (equity > 0) & market_cap.notna()
    book.loc[valid_book] = (
        equity.loc[valid_book] / market_cap.loc[valid_book]
    )
    book.loc[~np.isfinite(book)] = np.nan
    result["book_to_market"] = book

    return result


def _yield_ratio(
    numerator: pd.Series,
    market_cap: pd.Series,
) -> pd.Series:
    values = pd.Series(np.nan, index=market_cap.index, dtype="float64")
    valid = numerator.notna() & market_cap.notna() & (market_cap > 0)
    values.loc[valid] = numerator.loc[valid] / market_cap.loc[valid]
    values.loc[~np.isfinite(values)] = np.nan
    return values
