from __future__ import annotations

import numpy as np
import pandas as pd


RECENT_SKIP_WEEKS = 4
LONG_LOOKBACK_WEEKS = 52
MEDIUM_LOOKBACK_WEEKS = 26
MAX_CONTIGUOUS_GAP_DAYS = 14

RECENT_AGE_MIN_DAYS = 21
RECENT_AGE_MAX_DAYS = 35
LONG_AGE_MIN_DAYS = 350
LONG_AGE_MAX_DAYS = 378
MEDIUM_AGE_MIN_DAYS = 168
MEDIUM_AGE_MAX_DAYS = 196


def add_momentum_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add long-horizon momentum factors from weekly PIT return prices.

    V1 excludes approximately the most recent month:
      - 12m ex-1m = price around t-4w / price around t-52w - 1
      - 6m ex-1m  = price around t-4w / price around t-26w - 1

    History segments reset across gaps longer than 14 days and across canonical
    price-source or return-price-basis changes.
    """

    required = {"ticker", "decision_date", "return_price"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Momentum factors require columns: "
            + ", ".join(sorted(missing))
        )

    result = panel.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )
    result["return_price"] = pd.to_numeric(
        result["return_price"],
        errors="coerce",
    )

    original_index = result.index
    ordered = result.sort_values(
        ["ticker", "decision_date"],
        kind="stable",
    ).copy()

    grouped = ordered.groupby("ticker", sort=False)
    prior_date = grouped["decision_date"].shift(1)
    prior_price = grouped["return_price"].shift(1)
    gap_days = (ordered["decision_date"] - prior_date).dt.days

    source_change = pd.Series(False, index=ordered.index, dtype="bool")
    if "price_source" in ordered.columns:
        source = ordered["price_source"].fillna("").astype(str)
        prior_source = source.groupby(ordered["ticker"], sort=False).shift(1)
        source_change = prior_date.notna() & source.ne(prior_source)

    basis_change = pd.Series(False, index=ordered.index, dtype="bool")
    if "return_price_basis" in ordered.columns:
        basis = ordered["return_price_basis"].fillna("").astype(str)
        prior_basis = basis.groupby(ordered["ticker"], sort=False).shift(1)
        basis_change = prior_date.notna() & basis.ne(prior_basis)

    new_segment = (
        prior_date.isna()
        | gap_days.gt(MAX_CONTIGUOUS_GAP_DAYS)
        | ordered["return_price"].isna()
        | prior_price.isna()
        | ordered["return_price"].le(0)
        | prior_price.le(0)
        | source_change
        | basis_change
    )
    ordered["_momentum_segment"] = (
        new_segment.groupby(ordered["ticker"], sort=False).cumsum()
    )

    segment_group = ordered.groupby(
        ["ticker", "_momentum_segment"],
        sort=False,
    )

    recent_price = segment_group["return_price"].shift(RECENT_SKIP_WEEKS)
    long_price = segment_group["return_price"].shift(LONG_LOOKBACK_WEEKS)
    medium_price = segment_group["return_price"].shift(MEDIUM_LOOKBACK_WEEKS)

    recent_date = segment_group["decision_date"].shift(RECENT_SKIP_WEEKS)
    long_date = segment_group["decision_date"].shift(LONG_LOOKBACK_WEEKS)
    medium_date = segment_group["decision_date"].shift(MEDIUM_LOOKBACK_WEEKS)

    ordered["momentum_recent_price"] = recent_price
    ordered["momentum_12m_anchor_price"] = long_price
    ordered["momentum_6m_anchor_price"] = medium_price

    ordered["momentum_recent_age_days"] = (
        ordered["decision_date"] - recent_date
    ).dt.days
    ordered["momentum_12m_anchor_age_days"] = (
        ordered["decision_date"] - long_date
    ).dt.days
    ordered["momentum_6m_anchor_age_days"] = (
        ordered["decision_date"] - medium_date
    ).dt.days

    recent_age_valid = ordered["momentum_recent_age_days"].between(
        RECENT_AGE_MIN_DAYS,
        RECENT_AGE_MAX_DAYS,
        inclusive="both",
    )
    long_age_valid = ordered["momentum_12m_anchor_age_days"].between(
        LONG_AGE_MIN_DAYS,
        LONG_AGE_MAX_DAYS,
        inclusive="both",
    )
    medium_age_valid = ordered["momentum_6m_anchor_age_days"].between(
        MEDIUM_AGE_MIN_DAYS,
        MEDIUM_AGE_MAX_DAYS,
        inclusive="both",
    )

    ordered["momentum_12m_lookback_valid"] = (
        recent_age_valid
        & long_age_valid
        & recent_price.gt(0)
        & long_price.gt(0)
    )
    ordered["momentum_6m_lookback_valid"] = (
        recent_age_valid
        & medium_age_valid
        & recent_price.gt(0)
        & medium_price.gt(0)
    )

    long_signal = recent_price / long_price - 1.0
    medium_signal = recent_price / medium_price - 1.0

    ordered["momentum_12m_ex_1m"] = long_signal.where(
        ordered["momentum_12m_lookback_valid"]
    )
    ordered["momentum_6m_ex_1m"] = medium_signal.where(
        ordered["momentum_6m_lookback_valid"]
    )

    for column in ("momentum_12m_ex_1m", "momentum_6m_ex_1m"):
        ordered.loc[~np.isfinite(ordered[column]), column] = np.nan

    ordered["momentum_source_change"] = source_change
    ordered["momentum_basis_change"] = basis_change

    ordered = ordered.drop(columns=["_momentum_segment"])
    return ordered.loc[original_index].sort_index()
