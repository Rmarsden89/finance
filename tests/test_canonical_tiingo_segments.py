from __future__ import annotations

from datetime import date

from finance.data.historical_market_tickers import HistoricalMarketTickerOverride
from finance.data.prices import DailyPrice
from scripts.build_canonical_market_data import (
    coverage_status,
    tiingo_rows_for_windows,
)


def _price(ticker: str, day: date, value: float = 1.0) -> DailyPrice:
    return DailyPrice(
        ticker=ticker,
        date=day,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=1,
        adjusted_close=value,
        source="tiingo",
    )


def test_canonical_tiingo_rows_use_provider_share_class_symbol() -> None:
    cache = {
        "BRK-B": [
            _price("BRK-B", date(2015, 1, 2)),
            _price("BRK-B", date(2015, 1, 30)),
        ]
    }

    rows, symbols = tiingo_rows_for_windows(
        cache,
        pit_ticker="BRK.B",
        windows=[(date(2015, 1, 1), date(2015, 2, 1))],
        overrides=[],
    )

    assert symbols == ["BRK-B"]
    assert [row.ticker for row in rows] == ["BRK.B", "BRK.B"]
    status, start_gap, end_gap = coverage_status(
        rows,
        [(date(2015, 1, 1), date(2015, 2, 1))],
        tolerance_days=7,
    )
    assert status == "full_boundary_coverage"
    assert start_gap == 1
    assert end_gap == 1


def test_canonical_tiingo_rows_join_historical_alias_segments_same_provider() -> None:
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
            _price("CSC", date(2015, 1, 2)),
            _price("CSC", date(2015, 11, 30)),
        ],
        "DXC": [
            _price("DXC", date(2015, 12, 1), 2.0),
            _price("DXC", date(2015, 12, 31), 2.0),
        ],
    }

    rows, symbols = tiingo_rows_for_windows(
        cache,
        pit_ticker="DXC",
        windows=[(date(2015, 1, 1), date(2016, 1, 1))],
        overrides=overrides,
    )

    assert symbols == ["CSC", "DXC"]
    assert [row.date for row in rows] == [
        date(2015, 1, 2),
        date(2015, 11, 30),
        date(2015, 12, 1),
        date(2015, 12, 31),
    ]
    assert {row.source for row in rows} == {"tiingo"}
    assert {row.ticker for row in rows} == {"DXC"}

    status, _, _ = coverage_status(
        rows,
        [(date(2015, 1, 1), date(2016, 1, 1))],
        tolerance_days=7,
    )
    assert status == "full_boundary_coverage"


def test_canonical_tiingo_partial_history_stays_partial() -> None:
    cache = {
        "DOW": [
            _price("DOW", date(2019, 4, 2)),
            _price("DOW", date(2025, 12, 31)),
        ]
    }

    rows, _ = tiingo_rows_for_windows(
        cache,
        pit_ticker="DOW",
        windows=[(date(2015, 1, 1), date(2026, 1, 1))],
        overrides=[],
    )
    status, _, _ = coverage_status(
        rows,
        [(date(2015, 1, 1), date(2026, 1, 1))],
        tolerance_days=7,
    )

    assert status == "partial_boundary_coverage"


def test_direct_pit_symbol_can_preserve_full_history_before_segment_fallback() -> None:
    # Mirrors the canonical policy: if the PIT/current symbol already has a
    # complete Tiingo history, segmented aliases should not be required.
    windows = [(date(2015, 1, 1), date(2016, 1, 1))]
    direct_rows = [
        _price("AABA", date(2015, 1, 2)),
        _price("AABA", date(2015, 12, 31)),
    ]

    direct_status, start_gap, end_gap = coverage_status(
        direct_rows,
        windows,
        tolerance_days=7,
    )

    assert direct_status == "full_boundary_coverage"
    assert start_gap == 1
    assert end_gap == 0
