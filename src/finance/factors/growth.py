from __future__ import annotations

import numpy as np
import pandas as pd


LOOKBACK_WEEKS = 52
MIN_LOOKBACK_DAYS = 350
MAX_LOOKBACK_DAYS = 378


def add_growth_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add PIT-safe roughly one-year growth factors.

    The weekly panel already contains only information visible as of each
    decision date. We compare each ticker with its row 52 weekly observations
    earlier and require the calendar separation to be roughly one year.
    """

    required = {"ticker", "decision_date"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Growth factors require columns: " + ", ".join(sorted(missing))
        )

    result = panel.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )

    original_order = result.index
    ordered = result.sort_values(["ticker", "decision_date"]).copy()

    grouped = ordered.groupby("ticker", sort=False, group_keys=False)
    prior_date = grouped["decision_date"].shift(LOOKBACK_WEEKS)
    age_days = (ordered["decision_date"] - prior_date).dt.days
    valid_age = age_days.between(
        MIN_LOOKBACK_DAYS,
        MAX_LOOKBACK_DAYS,
        inclusive="both",
    )

    specs = {
        "revenue_growth_1y": ("revenue", "positive"),
        "net_income_growth_1y": ("net_income", "signed"),
        "operating_income_growth_1y": ("operating_income", "signed"),
        "operating_cash_flow_growth_1y": (
            "operating_cash_flow",
            "signed",
        ),
    }

    for output_column, (source_column, mode) in specs.items():
        current = pd.to_numeric(
            ordered.get(source_column),
            errors="coerce",
        )
        prior = grouped[source_column].shift(LOOKBACK_WEEKS)
        prior = pd.to_numeric(prior, errors="coerce")
        ordered[f"{source_column}_prior_52w"] = prior

        values = pd.Series(np.nan, index=ordered.index, dtype="float64")
        valid = valid_age & current.notna() & prior.notna()

        if mode == "positive":
            valid &= prior > 0
            denominator = prior
        else:
            valid &= prior != 0
            denominator = prior.abs()

        values.loc[valid] = (
            current.loc[valid] - prior.loc[valid]
        ) / denominator.loc[valid]
        values.loc[~np.isfinite(values)] = np.nan
        ordered[output_column] = values

    ordered["growth_lookback_days"] = age_days
    ordered["growth_lookback_valid"] = valid_age.fillna(False)

    return ordered.loc[original_order].sort_index()
