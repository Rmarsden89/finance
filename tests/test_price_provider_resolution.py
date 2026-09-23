from __future__ import annotations

from datetime import date

import pandas as pd

from finance.data.historical_market_tickers import HistoricalMarketTickerOverride
from finance.research.price_provider_resolution import (
    classify_resolution_state,
    segment_cache_summary,
    split_market_segments,
    tiingo_symbol,
)


def test_tiingo_symbol_translates_share_class_periods() -> None:
    assert tiingo_symbol("BRK.B") == "BRK-B"
    assert tiingo_symbol("BF.B") == "BF-B"
    assert tiingo_symbol("AAPL") == "AAPL"


def test_split_market_segments_applies_historical_override() -> None:
    overrides = [
        HistoricalMarketTickerOverride(
            pit_ticker="DXC",
            market_ticker="CSC",
            valid_from=date(2015, 1, 1),
            valid_to=date(2015, 12, 1),
            company_name="Computer Sciences Corporation",
            evidence="test",
        )
    ]

    segments = split_market_segments(
        pit_ticker="DXC",
        windows=[(date(2015, 1, 1), date(2016, 1, 1))],
        overrides=overrides,
    )

    assert segments[0][2:] == ("CSC", "CSC")
    assert segments[1][2:] == ("DXC", "DXC")


def test_segment_cache_summary_uses_expected_provider_symbols() -> None:
    result = segment_cache_summary(
        segments=[
            (date(2015, 1, 1), date(2016, 1, 1), "BRK.B", "BRK-B"),
        ],
        cache_index={"BRK-B": []},
    )

    assert result["expected_tiingo_symbols"] == "BRK-B"
    assert result["cached_expected_symbols"] == "BRK-B"
    assert result["all_expected_symbols_cached"] is True


def test_resolution_detects_old_share_class_symbol_mismatch() -> None:
    row = pd.Series({
        "failure_class": "identity_resolved|provider_missing",
        "selected_status": "missing",
        "tiingo_status": "missing",
        "stooq_status": "missing",
    })
    attempt = pd.Series({
        "status": "missing",
        "market_tickers_used": "BRK.B",
        "error": "",
    })

    assert classify_resolution_state(
        queue_row=row,
        expected_provider_symbols=["BRK-B"],
        cache_symbols=set(),
        attempt_row=attempt,
    ) == "provider_symbol_translation_mismatch"


def test_resolution_preserves_explicit_quality_exclusion() -> None:
    row = pd.Series({
        "failure_class": "identity_resolved|provider_quality_excluded",
        "selected_status": "missing",
        "tiingo_status": "missing",
        "stooq_status": "quality_excluded",
    })

    assert classify_resolution_state(
        queue_row=row,
        expected_provider_symbols=["PARA"],
        cache_symbols=set(),
        attempt_row=None,
    ) == "explicit_quality_exclusion"
