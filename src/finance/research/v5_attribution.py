from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from finance.backtest import BacktestPriceStore


DEFAULT_HORIZONS_WEEKS = (1, 4, 13, 26, 52)
RANK_BANDS = ((1, 10, "top10"), (11, 25, "rank11_25"), (26, 50, "rank26_50"))


@dataclass(frozen=True)
class AttributionBuildSummary:
    rows: int
    decision_dates: int
    unique_tickers: int
    top_conviction_rows: int
    ranked_rows: int
    duplicate_keys: int
    horizons_weeks: tuple[int, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y"})
    )


def add_v1_rank_fields(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    required = {"decision_date", "ticker", "long_growth_v1_score", "top_conviction_eligible"}
    missing = sorted(required - set(result.columns))
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))

    result["decision_date"] = pd.to_datetime(result["decision_date"], errors="raise").dt.date
    result["ticker"] = result["ticker"].astype(str).str.strip().str.upper()
    if result[["decision_date", "ticker"]].duplicated().any():
        raise ValueError("Duplicate decision_date/ticker rows are not allowed")

    result["long_growth_v1_score"] = pd.to_numeric(
        result["long_growth_v1_score"], errors="coerce"
    )
    eligible = _bool_series(result["top_conviction_eligible"])
    result["top_conviction_eligible"] = eligible

    result["v1_rank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    for _, idx in result.groupby("decision_date", sort=False).groups.items():
        group = result.loc[idx]
        candidates = group.loc[
            group["top_conviction_eligible"] & group["long_growth_v1_score"].notna()
        ].copy()
        candidates = candidates.sort_values(
            ["long_growth_v1_score", "ticker"],
            ascending=[False, True],
            kind="mergesort",
        )
        result.loc[candidates.index, "v1_rank"] = pd.array(
            range(1, len(candidates) + 1), dtype="Int64"
        )

    result["v1_top10"] = result["v1_rank"].between(1, 10).fillna(False)
    result["v1_top25"] = result["v1_rank"].between(1, 25).fillna(False)

    band = pd.Series("unranked", index=result.index, dtype="string")
    for lower, upper, label in RANK_BANDS:
        mask = result["v1_rank"].between(lower, upper).fillna(False)
        band.loc[mask] = label
    ranked_other = result["v1_rank"].notna() & band.eq("unranked")
    band.loc[ranked_other] = "rank51_plus"
    result["v1_rank_band"] = band
    return result


def _first_quote_on_or_after(
    store: BacktestPriceStore,
    ticker: str,
    target: date,
    *,
    max_delay_days: int,
):
    # next_after() is strictly after its date argument; subtract one day so an
    # exact target-date quote remains eligible.
    quote = store.next_after(
        ticker,
        target - timedelta(days=1),
        max_delay_days=max_delay_days + 1,
    )
    if quote is None:
        return None
    if quote.date < target or (quote.date - target).days > max_delay_days:
        return None
    return quote


def add_forward_returns(
    frame: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    horizons_weeks: Iterable[int] = DEFAULT_HORIZONS_WEEKS,
    max_exit_delay_days: int = 7,
    benchmark_ticker: str = "SPY",
) -> pd.DataFrame:
    result = frame.copy()
    if "decision_date" not in result.columns or "ticker" not in result.columns:
        raise ValueError("Forward returns require decision_date and ticker")

    result["decision_date"] = pd.to_datetime(result["decision_date"], errors="raise").dt.date
    result["ticker"] = result["ticker"].astype(str).str.strip().str.upper()

    entry_prices = []
    entry_dates = []
    entry_sources = []
    benchmark_entry_prices = []
    benchmark_entry_dates = []

    for row in result.itertuples(index=False):
        decision_day = row.decision_date
        ticker = str(row.ticker)
        entry = price_store.latest_as_of(ticker, decision_day)
        benchmark_entry = price_store.latest_as_of(benchmark_ticker, decision_day)

        entry_prices.append(entry.mark_price if entry is not None else np.nan)
        entry_dates.append(entry.date if entry is not None else pd.NaT)
        entry_sources.append(entry.source if entry is not None else None)
        benchmark_entry_prices.append(
            benchmark_entry.mark_price if benchmark_entry is not None else np.nan
        )
        benchmark_entry_dates.append(
            benchmark_entry.date if benchmark_entry is not None else pd.NaT
        )

    result["forward_entry_price"] = entry_prices
    result["forward_entry_price_date"] = entry_dates
    result["forward_entry_price_source"] = entry_sources
    result["spy_entry_price"] = benchmark_entry_prices
    result["spy_entry_price_date"] = benchmark_entry_dates

    for weeks in tuple(int(value) for value in horizons_weeks):
        if weeks <= 0:
            raise ValueError("Forward-return horizons must be positive")

        stock_returns = []
        stock_exit_dates = []
        spy_returns = []
        spy_exit_dates = []
        relative_returns = []
        status = []

        for row in result.itertuples(index=False):
            target = row.decision_date + timedelta(weeks=weeks)
            entry_price = float(row.forward_entry_price) if pd.notna(row.forward_entry_price) else np.nan
            spy_entry = float(row.spy_entry_price) if pd.notna(row.spy_entry_price) else np.nan

            exit_quote = _first_quote_on_or_after(
                price_store,
                str(row.ticker),
                target,
                max_delay_days=max_exit_delay_days,
            )
            spy_exit = _first_quote_on_or_after(
                price_store,
                benchmark_ticker,
                target,
                max_delay_days=max_exit_delay_days,
            )

            stock_return = (
                exit_quote.mark_price / entry_price - 1.0
                if exit_quote is not None and np.isfinite(entry_price) and entry_price > 0
                else np.nan
            )
            spy_return = (
                spy_exit.mark_price / spy_entry - 1.0
                if spy_exit is not None and np.isfinite(spy_entry) and spy_entry > 0
                else np.nan
            )
            relative = (
                stock_return - spy_return
                if np.isfinite(stock_return) and np.isfinite(spy_return)
                else np.nan
            )

            stock_returns.append(stock_return)
            stock_exit_dates.append(exit_quote.date if exit_quote is not None else pd.NaT)
            spy_returns.append(spy_return)
            spy_exit_dates.append(spy_exit.date if spy_exit is not None else pd.NaT)
            relative_returns.append(relative)

            if not np.isfinite(entry_price):
                status.append("missing_entry_price")
            elif exit_quote is None:
                status.append("missing_exit_price")
            elif not np.isfinite(spy_entry):
                status.append("missing_spy_entry")
            elif spy_exit is None:
                status.append("missing_spy_exit")
            else:
                status.append("mature")

        prefix = f"fwd_{weeks}w"
        result[f"{prefix}_return"] = stock_returns
        result[f"{prefix}_exit_date"] = stock_exit_dates
        result[f"{prefix}_spy_return"] = spy_returns
        result[f"{prefix}_spy_exit_date"] = spy_exit_dates
        result[f"{prefix}_excess_return"] = relative_returns
        result[f"{prefix}_status"] = status

    return result


def build_v5_attribution_dataset(
    scored_history: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    horizons_weeks: Iterable[int] = DEFAULT_HORIZONS_WEEKS,
    max_exit_delay_days: int = 7,
) -> tuple[pd.DataFrame, AttributionBuildSummary]:
    ranked = add_v1_rank_fields(scored_history)
    output = add_forward_returns(
        ranked,
        price_store=price_store,
        horizons_weeks=horizons_weeks,
        max_exit_delay_days=max_exit_delay_days,
    )
    duplicate_keys = int(output[["decision_date", "ticker"]].duplicated().sum())
    if duplicate_keys:
        raise ValueError(f"Duplicate output keys: {duplicate_keys}")

    horizons = tuple(int(value) for value in horizons_weeks)
    summary = AttributionBuildSummary(
        rows=len(output),
        decision_dates=int(output["decision_date"].nunique()),
        unique_tickers=int(output["ticker"].nunique()),
        top_conviction_rows=int(_bool_series(output["top_conviction_eligible"]).sum()),
        ranked_rows=int(output["v1_rank"].notna().sum()),
        duplicate_keys=duplicate_keys,
        horizons_weeks=horizons,
    )
    return output, summary
