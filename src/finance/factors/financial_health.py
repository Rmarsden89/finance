from __future__ import annotations

import numpy as np
import pandas as pd


def add_financial_health_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add raw balance-sheet and cash-generation health factors."""

    result = panel.copy()

    assets = pd.to_numeric(result.get("total_assets"), errors="coerce")
    liabilities = pd.to_numeric(
        result.get("total_liabilities"),
        errors="coerce",
    )
    cash = pd.to_numeric(result.get("cash"), errors="coerce")
    operating_cash_flow = pd.to_numeric(
        result.get("operating_cash_flow"),
        errors="coerce",
    )

    result["liabilities_to_assets"] = _positive_denominator_ratio(
        liabilities,
        assets,
    )
    result["cash_to_assets"] = _positive_denominator_ratio(
        cash,
        assets,
    )
    result["operating_cash_flow_to_liabilities"] = (
        _positive_denominator_ratio(
            operating_cash_flow,
            liabilities,
        )
    )

    positive = pd.Series(np.nan, index=result.index, dtype="float64")
    known = operating_cash_flow.notna()
    positive.loc[known] = (operating_cash_flow.loc[known] > 0).astype(float)
    result["positive_operating_cash_flow"] = positive

    return result


def _positive_denominator_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    valid = (
        numerator.notna()
        & denominator.notna()
        & (denominator > 0)
    )
    values = pd.Series(np.nan, index=numerator.index, dtype="float64")
    values.loc[valid] = numerator.loc[valid] / denominator.loc[valid]
    values.loc[~np.isfinite(values)] = np.nan
    return values
