from __future__ import annotations

import math

import numpy as np
import pandas as pd


LOOKBACK_RETURNS = 52
LOOKBACK_PRICES = 53
MIN_VALID_RETURNS = 40
MIN_VALID_PRICES = 41
MAX_CONTIGUOUS_GAP_DAYS = 14


def add_stability_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """Add trailing price-stability factors from PIT weekly return prices.

    Return chains are broken across membership/data gaps longer than 14 days.
    The trailing window is 52 weekly returns and requires at least 40 valid
    observations before a stability metric is emitted.
    """

    required = {"ticker", "decision_date", "return_price"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Stability factors require columns: "
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
        source_change = (
            prior_date.notna()
            & source.ne(prior_source)
        )

    basis_change = pd.Series(False, index=ordered.index, dtype="bool")
    if "return_price_basis" in ordered.columns:
        basis = ordered["return_price_basis"].fillna("").astype(str)
        prior_basis = basis.groupby(ordered["ticker"], sort=False).shift(1)
        basis_change = (
            prior_date.notna()
            & basis.ne(prior_basis)
        )

    continuous = (
        gap_days.notna()
        & gap_days.le(MAX_CONTIGUOUS_GAP_DAYS)
        & ordered["return_price"].notna()
        & prior_price.notna()
        & ordered["return_price"].gt(0)
        & prior_price.gt(0)
        & ~source_change
        & ~basis_change
    )

    weekly_return = pd.Series(
        np.nan,
        index=ordered.index,
        dtype="float64",
    )
    weekly_return.loc[continuous] = (
        ordered.loc[continuous, "return_price"]
        / prior_price.loc[continuous]
        - 1.0
    )
    weekly_return.loc[~np.isfinite(weekly_return)] = np.nan
    ordered["weekly_return"] = weekly_return
    ordered["return_gap_days"] = gap_days

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
    ordered["_stability_segment"] = (
        new_segment.groupby(ordered["ticker"]).cumsum()
    )

    segment_keys = [ordered["ticker"], ordered["_stability_segment"]]

    return_count = (
        ordered["weekly_return"]
        .groupby(segment_keys, sort=False)
        .rolling(LOOKBACK_RETURNS, min_periods=1)
        .count()
        .reset_index(level=[0, 1], drop=True)
        .reindex(ordered.index)
    )
    ordered["stability_return_count_52w"] = return_count

    volatility = (
        ordered["weekly_return"]
        .groupby(segment_keys, sort=False)
        .rolling(LOOKBACK_RETURNS, min_periods=MIN_VALID_RETURNS)
        .std(ddof=1)
        .reset_index(level=[0, 1], drop=True)
        .reindex(ordered.index)
        * math.sqrt(52.0)
    )
    ordered["volatility_52w"] = volatility

    downside_square = ordered["weekly_return"].clip(upper=0).pow(2)
    downside_mean_square = (
        downside_square
        .groupby(segment_keys, sort=False)
        .rolling(LOOKBACK_RETURNS, min_periods=MIN_VALID_RETURNS)
        .mean()
        .reset_index(level=[0, 1], drop=True)
        .reindex(ordered.index)
    )
    ordered["downside_deviation_52w"] = (
        np.sqrt(downside_mean_square) * math.sqrt(52.0)
    )

    price_count = (
        ordered["return_price"]
        .groupby(segment_keys, sort=False)
        .rolling(LOOKBACK_PRICES, min_periods=1)
        .count()
        .reset_index(level=[0, 1], drop=True)
        .reindex(ordered.index)
    )
    ordered["stability_price_count_52w"] = price_count

    max_drawdown = (
        ordered["return_price"]
        .groupby(segment_keys, sort=False)
        .rolling(LOOKBACK_PRICES, min_periods=MIN_VALID_PRICES)
        .apply(_max_drawdown_magnitude, raw=True)
        .reset_index(level=[0, 1], drop=True)
        .reindex(ordered.index)
    )
    ordered["max_drawdown_52w"] = max_drawdown

    ordered["stability_source_change"] = source_change
    ordered["stability_basis_change"] = basis_change

    ordered = ordered.drop(columns=["_stability_segment"])
    return ordered.loc[original_index].sort_index()


def _max_drawdown_magnitude(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    finite = finite[finite > 0]
    if len(finite) < MIN_VALID_PRICES:
        return np.nan

    peaks = np.maximum.accumulate(finite)
    drawdowns = finite / peaks - 1.0
    return float(-np.min(drawdowns))
