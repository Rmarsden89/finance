from __future__ import annotations

import numpy as np
import pandas as pd


def add_quality_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add raw quality factors without normalization or weighting."""

    result = panel.copy()

    assets = pd.to_numeric(result.get("total_assets"), errors="coerce")
    equity = pd.to_numeric(
        result.get("shareholders_equity"),
        errors="coerce",
    )
    revenue = pd.to_numeric(result.get("revenue"), errors="coerce")
    net_income = pd.to_numeric(result.get("net_income"), errors="coerce")
    operating_income = pd.to_numeric(
        result.get("operating_income"),
        errors="coerce",
    )
    operating_cash_flow = pd.to_numeric(
        result.get("operating_cash_flow"),
        errors="coerce",
    )
    capital_expenditures = pd.to_numeric(
        result.get("capital_expenditures"),
        errors="coerce",
    )

    result["return_on_assets"] = _safe_ratio(
        net_income,
        assets,
        denominator_must_be_positive=True,
    )
    result["return_on_equity"] = _safe_ratio(
        net_income,
        equity,
        denominator_must_be_positive=True,
    )
    result["operating_margin"] = _safe_ratio(
        operating_income,
        revenue,
        denominator_must_be_positive=True,
    )

    free_cash_flow = operating_cash_flow - capital_expenditures
    result["free_cash_flow_margin"] = _safe_ratio(
        free_cash_flow,
        revenue,
        denominator_must_be_positive=True,
    )

    return result


def _safe_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
    *,
    denominator_must_be_positive: bool,
) -> pd.Series:
    valid = numerator.notna() & denominator.notna()
    if denominator_must_be_positive:
        valid &= denominator > 0
    else:
        valid &= denominator != 0

    values = pd.Series(np.nan, index=numerator.index, dtype="float64")
    values.loc[valid] = numerator.loc[valid] / denominator.loc[valid]
    values.loc[~np.isfinite(values)] = np.nan
    return values
