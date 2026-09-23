from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from finance.data.historical_market_tickers import (
    HistoricalMarketTickerOverride,
    market_ticker_as_of,
)


def tiingo_symbol(symbol: str) -> str:
    """Translate canonical market ticker spelling to Tiingo symbology."""

    return symbol.strip().upper().replace(".", "-")


def split_market_segments(
    *,
    pit_ticker: str,
    windows: list[tuple[date, date]],
    overrides: list[HistoricalMarketTickerOverride],
) -> list[tuple[date, date, str, str]]:
    """Return PIT market segments plus canonical and Tiingo provider symbols."""

    segments: list[tuple[date, date, str, str]] = []
    for window_start, window_end in windows:
        boundaries = {window_start, window_end}
        for row in overrides:
            if row.pit_ticker != pit_ticker.upper():
                continue
            if window_start < row.valid_from < window_end:
                boundaries.add(row.valid_from)
            if row.valid_to is not None and window_start < row.valid_to < window_end:
                boundaries.add(row.valid_to)

        ordered = sorted(boundaries)
        for left, right in zip(ordered, ordered[1:]):
            if left >= right:
                continue
            market_ticker = market_ticker_as_of(
                overrides,
                pit_ticker=pit_ticker,
                as_of=left,
            )
            segments.append(
                (
                    left,
                    right,
                    market_ticker,
                    tiingo_symbol(market_ticker),
                )
            )
    return segments


def cache_files_by_symbol(cache_dir: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in sorted(cache_dir.glob("*.csv")):
        if path.name.lower().endswith("coverage.csv"):
            continue
        symbol = path.name.split("_", 1)[0].upper()
        result.setdefault(symbol, []).append(path)
    return result


def report_attempt_map(report: pd.DataFrame) -> dict[str, pd.Series]:
    if report.empty or "pit_ticker" not in report.columns:
        return {}
    result: dict[str, pd.Series] = {}
    for _, row in report.iterrows():
        ticker = str(row.get("pit_ticker") or "").strip().upper()
        if ticker:
            result[ticker] = row
    return result


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def classify_resolution_state(
    *,
    queue_row: pd.Series,
    expected_provider_symbols: list[str],
    cache_symbols: set[str],
    attempt_row: pd.Series | None,
) -> str:
    """Classify why one unresolved ticker remains unresolved."""

    selected_status = _text(queue_row.get("selected_status"))
    tiingo_status = _text(queue_row.get("tiingo_status"))
    stooq_status = _text(queue_row.get("stooq_status"))

    if (
        str(queue_row.get("failure_class") or "").endswith(
            "provider_quality_excluded"
        )
    ):
        return "explicit_quality_exclusion"

    expected = {symbol.upper() for symbol in expected_provider_symbols}
    cached = expected & {symbol.upper() for symbol in cache_symbols}

    if attempt_row is None:
        if cached:
            return "cache_present_not_rebuilt_into_canonical"
        return "not_recorded_in_priority_audit"

    attempt_status = _text(attempt_row.get("status"))
    attempt_error = _text(attempt_row.get("error"))
    attempted_symbols = {
        item.strip().upper()
        for item in _text(
            attempt_row.get("market_tickers_used")
        ).split("|")
        if item.strip()
    }

    # Historical priority audit used canonical symbols, while Tiingo requires
    # hyphens for share classes. Detect the old mismatch explicitly.
    punctuation_mismatch = any(
        "." in canonical
        and tiingo_symbol(canonical) in expected
        and canonical in attempted_symbols
        and tiingo_symbol(canonical) not in attempted_symbols
        for canonical in attempted_symbols
    )
    if punctuation_mismatch:
        return "provider_symbol_translation_mismatch"

    if attempt_error:
        text = attempt_error.lower()
        if any(
            marker in text
            for marker in (
                "429",
                "too many requests",
                "empty response",
                "non-json",
                "expecting value",
            )
        ):
            return "priority_audit_throttled_or_transient"
        return "priority_audit_provider_error"

    if attempt_status == "full_boundary_coverage":
        return "full_tiingo_recovery_not_rebuilt_into_canonical"
    if attempt_status == "partial_boundary_coverage":
        return "tiingo_partial_boundary_coverage"
    if attempt_status == "missing":
        return "tiingo_missing_after_recorded_attempt"

    if cached:
        return "cache_present_not_rebuilt_into_canonical"

    if selected_status == "partial_boundary_coverage":
        return "canonical_partial_needs_reaudit"
    if tiingo_status == "missing" and stooq_status == "missing":
        return "both_providers_missing_unverified_attempt"
    return "unresolved_other"


def segment_cache_summary(
    *,
    segments: list[tuple[date, date, str, str]],
    cache_index: dict[str, list[Path]],
) -> dict[str, object]:
    canonical = []
    provider = []
    cached = []
    for _, _, market_ticker, provider_symbol in segments:
        if market_ticker not in canonical:
            canonical.append(market_ticker)
        if provider_symbol not in provider:
            provider.append(provider_symbol)
        if provider_symbol in cache_index and provider_symbol not in cached:
            cached.append(provider_symbol)

    return {
        "canonical_market_tickers": "|".join(canonical),
        "expected_tiingo_symbols": "|".join(provider),
        "expected_symbol_count": len(provider),
        "cached_expected_symbols": "|".join(cached),
        "cached_expected_symbol_count": len(cached),
        "all_expected_symbols_cached": (
            bool(provider) and len(cached) == len(provider)
        ),
    }
