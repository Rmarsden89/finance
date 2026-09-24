from __future__ import annotations

from datetime import date

import pandas as pd

from finance.data.historical_market_tickers import HistoricalMarketTickerOverride
from finance.data.prices import DailyPrice
from finance.research.price_provider_resolution import (
    cached_coverage_status,
    cached_tiingo_rows_for_windows,
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


def test_nan_priority_error_is_not_treated_as_provider_error() -> None:
    row = pd.Series({
        "failure_class": "identity_resolved|provider_missing",
        "selected_status": "missing",
        "tiingo_status": "missing",
        "stooq_status": "missing",
    })
    attempt = pd.Series({
        "status": "missing",
        "market_tickers_used": "BK",
        "error": float("nan"),
    })

    assert classify_resolution_state(
        queue_row=row,
        expected_provider_symbols=["BK"],
        cache_symbols=set(),
        attempt_row=attempt,
    ) == "tiingo_missing_after_recorded_attempt"


def test_cached_tiingo_recovery_combines_historical_alias_segments() -> None:
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
    cache = {
        "CSC": [
            DailyPrice(
                ticker="CSC",
                date=date(2015, 1, 2),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1,
                adjusted_close=1.0,
                source="tiingo",
            ),
            DailyPrice(
                ticker="CSC",
                date=date(2015, 11, 30),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1,
                adjusted_close=1.0,
                source="tiingo",
            ),
        ],
        "DXC": [
            DailyPrice(
                ticker="DXC",
                date=date(2015, 12, 1),
                open=2.0,
                high=2.0,
                low=2.0,
                close=2.0,
                volume=1,
                adjusted_close=2.0,
                source="tiingo",
            ),
            DailyPrice(
                ticker="DXC",
                date=date(2015, 12, 31),
                open=2.0,
                high=2.0,
                low=2.0,
                close=2.0,
                volume=1,
                adjusted_close=2.0,
                source="tiingo",
            ),
        ],
    }

    rows, symbols = cached_tiingo_rows_for_windows(
        pit_ticker="DXC",
        windows=[(date(2015, 1, 1), date(2016, 1, 1))],
        overrides=overrides,
        cache=cache,
    )

    assert symbols == ["CSC", "DXC"]
    assert [row.date for row in rows] == [
        date(2015, 1, 2),
        date(2015, 11, 30),
        date(2015, 12, 1),
        date(2015, 12, 31),
    ]
    status, start_gap, end_gap = cached_coverage_status(
        rows,
        [(date(2015, 1, 1), date(2016, 1, 1))],
        tolerance_days=7,
    )
    assert status == "full_boundary_coverage"
    assert start_gap == 1
    assert end_gap == 0


def test_cached_tiingo_recovery_uses_translated_share_class_symbol() -> None:
    cache = {
        "BRK-B": [
            DailyPrice(
                ticker="BRK-B",
                date=date(2015, 1, 2),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1,
                adjusted_close=1.0,
                source="tiingo",
            )
        ]
    }

    rows, symbols = cached_tiingo_rows_for_windows(
        pit_ticker="BRK.B",
        windows=[(date(2015, 1, 1), date(2015, 1, 3))],
        overrides=[],
        cache=cache,
    )

    assert symbols == ["BRK-B"]
    assert len(rows) == 1
