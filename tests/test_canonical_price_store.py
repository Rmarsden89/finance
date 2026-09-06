from __future__ import annotations

import csv
import gzip
from datetime import date

from finance.data.research_panel import CanonicalPriceStore


def test_canonical_price_store_reads_pit_ticker_and_respects_as_of(tmp_path) -> None:
    prices = tmp_path / "daily_prices.csv.gz"

    with gzip.open(prices, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "pit_ticker",
                "market_ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "adjusted_close",
                "volume",
                "source",
            ]
        )
        writer.writerow(
            [
                "PEAK",
                "HCP",
                "2019-11-01",
                "34.0",
                "35.0",
                "33.5",
                "34.5",
                "34.25",
                "1000",
                "tiingo",
            ]
        )
        writer.writerow(
            [
                "PEAK",
                "PEAK",
                "2019-11-06",
                "35.0",
                "36.0",
                "34.5",
                "35.5",
                "",
                "1100",
                "stooq_bulk",
            ]
        )

    store = CanonicalPriceStore(prices)

    before_change = store.latest_as_of("PEAK", date(2019, 11, 5))
    assert before_change is not None
    assert before_change["date"] == date(2019, 11, 1)
    assert before_change["market_ticker"] == "HCP"
    assert before_change["source"] == "tiingo"
    assert before_change["adjusted_close"] == 34.25

    after_change = store.latest_as_of("PEAK", date(2019, 11, 8))
    assert after_change is not None
    assert after_change["date"] == date(2019, 11, 6)
    assert after_change["market_ticker"] == "PEAK"
    assert after_change["source"] == "stooq_bulk"
    assert after_change["adjusted_close"] is None

    assert store.latest_as_of("PEAK", date(2019, 10, 31)) is None
    assert store.latest_as_of("MISSING", date(2019, 11, 8)) is None
