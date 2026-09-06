from __future__ import annotations

import argparse
import csv
import gzip
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from finance.data.historical_market_tickers import (
    HistoricalMarketTickerOverride,
    load_historical_market_ticker_overrides,
    market_ticker_as_of,
)
from finance.data.prices import DailyPrice
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.stooq import StooqBulkArchive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build canonical PIT market-price data using Tiingo as primary "
            "and Stooq bulk as fallback."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--tiingo-cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument("--stooq-archive", type=Path, required=True)
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--boundary-tolerance-days", type=int, default=7)
    parser.add_argument(
        "--stooq-exclusions",
        type=Path,
        default=Path("data/reference/stooq_quality_exclusions.csv"),
        help="Date-bounded PIT tickers that must not use Stooq as fallback.",
    )
    parser.add_argument(
        "--coverage-output",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--prices-output",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    return parser.parse_args()


def membership_windows(intervals, *, start: date, end: date):
    result = defaultdict(list)
    end_exclusive = end + timedelta(days=1)
    for interval in intervals:
        interval_end = interval.end_date or end_exclusive
        window_start = max(interval.start_date, start)
        window_end = min(interval_end, end_exclusive)
        if window_start < window_end:
            result[interval.ticker].append((window_start, window_end))
    return dict(result)


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: str | None) -> int | None:
    number = _float_or_none(value)
    return int(number) if number is not None else None


@dataclass(frozen=True)
class StooqQualityExclusion:
    pit_ticker: str
    valid_from: date
    valid_to: date | None
    reason: str

    def overlaps(self, start: date, end_exclusive: date) -> bool:
        exclusion_end = self.valid_to or date.max
        return self.valid_from < end_exclusive and exclusion_end >= start


def load_stooq_exclusions(path: Path) -> list[StooqQualityExclusion]:
    if not path.exists():
        return []

    rows: list[StooqQualityExclusion] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pit_ticker = (row.get("pit_ticker") or "").strip().upper()
            if not pit_ticker:
                continue
            rows.append(
                StooqQualityExclusion(
                    pit_ticker=pit_ticker,
                    valid_from=date.fromisoformat(row["valid_from"]),
                    valid_to=(
                        date.fromisoformat(row["valid_to"])
                        if (row.get("valid_to") or "").strip()
                        else None
                    ),
                    reason=(row.get("reason") or "").strip(),
                )
            )
    return rows


def stooq_exclusion_reason(
    exclusions: list[StooqQualityExclusion],
    *,
    pit_ticker: str,
    windows: list[tuple[date, date]],
) -> str | None:
    for row in exclusions:
        if row.pit_ticker != pit_ticker.upper():
            continue
        if any(row.overlaps(start, end) for start, end in windows):
            return row.reason or "Stooq quality exclusion"
    return None


def load_tiingo_cache(cache_dir: Path) -> dict[str, list[DailyPrice]]:
    by_symbol: dict[str, dict[date, DailyPrice]] = defaultdict(dict)

    for path in sorted(cache_dir.glob("*.csv")):
        if path.name.lower().endswith("coverage.csv"):
            continue
        symbol = path.name.split("_", 1)[0].upper()

        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "date" not in reader.fieldnames:
                continue

            for row in reader:
                try:
                    price_date = date.fromisoformat(row["date"])
                except (KeyError, TypeError, ValueError):
                    continue

                close = _float_or_none(row.get("close"))
                if close is None:
                    continue

                by_symbol[symbol][price_date] = DailyPrice(
                    ticker=symbol,
                    date=price_date,
                    open=_float_or_none(row.get("open")) or close,
                    high=_float_or_none(row.get("high")) or close,
                    low=_float_or_none(row.get("low")) or close,
                    close=close,
                    volume=_int_or_none(row.get("volume")),
                    adjusted_close=_float_or_none(row.get("adjusted_close")),
                    source="tiingo",
                )

    return {
        symbol: [rows[value] for value in sorted(rows)]
        for symbol, rows in by_symbol.items()
    }


def split_market_segments(
    *,
    pit_ticker: str,
    start: date,
    end_exclusive: date,
    overrides: list[HistoricalMarketTickerOverride],
) -> list[tuple[date, date, str]]:
    boundaries = {start, end_exclusive}
    for row in overrides:
        if row.pit_ticker != pit_ticker.upper():
            continue
        if start < row.valid_from < end_exclusive:
            boundaries.add(row.valid_from)
        if row.valid_to is not None and start < row.valid_to < end_exclusive:
            boundaries.add(row.valid_to)

    ordered = sorted(boundaries)
    segments = []
    for left, right in zip(ordered, ordered[1:]):
        market_ticker = market_ticker_as_of(
            overrides,
            pit_ticker=pit_ticker,
            as_of=left,
        )
        segments.append((left, right, market_ticker))
    return segments


def rows_in_windows(
    prices: list[DailyPrice],
    windows: list[tuple[date, date]],
) -> list[DailyPrice]:
    return [
        row
        for row in prices
        if any(start <= row.date < end for start, end in windows)
    ]


def stooq_rows_for_windows(
    archive: StooqBulkArchive,
    *,
    pit_ticker: str,
    windows: list[tuple[date, date]],
    overrides: list[HistoricalMarketTickerOverride],
) -> tuple[list[DailyPrice], list[str]]:
    by_date: dict[date, DailyPrice] = {}
    symbols_used: list[str] = []

    for window_start, window_end in windows:
        for segment_start, segment_end, market_ticker in split_market_segments(
            pit_ticker=pit_ticker,
            start=window_start,
            end_exclusive=window_end,
            overrides=overrides,
        ):
            if market_ticker not in symbols_used:
                symbols_used.append(market_ticker)

            for row in archive.daily_prices(
                market_ticker,
                start=segment_start,
                end=segment_end - timedelta(days=1),
            ):
                by_date[row.date] = DailyPrice(
                    ticker=pit_ticker,
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    adjusted_close=row.adjusted_close,
                    source="stooq_bulk",
                )

    return [by_date[value] for value in sorted(by_date)], symbols_used


def coverage_status(
    prices: list[DailyPrice],
    windows: list[tuple[date, date]],
    *,
    tolerance_days: int,
) -> tuple[str, int | None, int | None]:
    if not prices:
        return "missing", None, None

    membership_start = min(start for start, _ in windows)
    membership_end = max(end for _, end in windows) - timedelta(days=1)
    first_price = prices[0].date
    last_price = prices[-1].date

    start_gap = (first_price - membership_start).days
    end_gap = (membership_end - last_price).days

    if start_gap <= tolerance_days and end_gap <= tolerance_days:
        return "full_boundary_coverage", start_gap, end_gap
    return "partial_boundary_coverage", start_gap, end_gap


def main() -> None:
    args = parse_args()
    start = date(args.start_year, 1, 1)
    end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows_by_ticker = membership_windows(intervals, start=start, end=end)
    overrides = load_historical_market_ticker_overrides(
        args.historical_market_tickers
    )
    stooq_exclusions = load_stooq_exclusions(args.stooq_exclusions)

    print("Loading Tiingo cache...")
    tiingo = load_tiingo_cache(args.tiingo_cache_dir)
    print(f"Tiingo symbols cached: {len(tiingo):,}")

    print("Indexing Stooq bulk archive...")
    stooq = StooqBulkArchive(args.stooq_archive)
    print(f"Stooq symbols indexed: {len(stooq.symbols()):,}")
    print(f"Stooq exclusions loaded: {len(stooq_exclusions):,}")
    print()

    coverage_rows = []
    selected_prices = []

    for number, pit_ticker in enumerate(sorted(windows_by_ticker), start=1):
        windows = windows_by_ticker[pit_ticker]
        membership_start = min(value[0] for value in windows)
        membership_end_exclusive = max(value[1] for value in windows)

        tiingo_rows = rows_in_windows(tiingo.get(pit_ticker, []), windows)
        tiingo_status, tiingo_start_gap, tiingo_end_gap = coverage_status(
            tiingo_rows,
            windows,
            tolerance_days=args.boundary_tolerance_days,
        )

        stooq_rows: list[DailyPrice] = []
        stooq_symbols: list[str] = []
        stooq_status = "not_checked"
        stooq_start_gap = None
        stooq_end_gap = None
        stooq_exclusion = stooq_exclusion_reason(
            stooq_exclusions,
            pit_ticker=pit_ticker,
            windows=windows,
        )

        if tiingo_status == "full_boundary_coverage":
            selected_source = "tiingo"
            selected_status = tiingo_status
            chosen = tiingo_rows
        else:
            if stooq_exclusion:
                stooq_status = "quality_excluded"
            else:
                stooq_rows, stooq_symbols = stooq_rows_for_windows(
                    stooq,
                    pit_ticker=pit_ticker,
                    windows=windows,
                    overrides=overrides,
                )
                stooq_status, stooq_start_gap, stooq_end_gap = coverage_status(
                    stooq_rows,
                    windows,
                    tolerance_days=args.boundary_tolerance_days,
                )

            if stooq_status == "full_boundary_coverage":
                selected_source = "stooq_bulk"
                selected_status = stooq_status
                chosen = stooq_rows
            else:
                selected_source = "unresolved"
                selected_status = (
                    "partial_boundary_coverage"
                    if (
                        tiingo_status == "partial_boundary_coverage"
                        or stooq_status == "partial_boundary_coverage"
                    )
                    else "missing"
                )
                chosen = []

        for row in chosen:
            market_ticker = market_ticker_as_of(
                overrides,
                pit_ticker=pit_ticker,
                as_of=row.date,
            )
            selected_prices.append(
                {
                    "pit_ticker": pit_ticker,
                    "market_ticker": market_ticker,
                    "date": row.date.isoformat(),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "adjusted_close": (
                        "" if row.adjusted_close is None else row.adjusted_close
                    ),
                    "volume": "" if row.volume is None else row.volume,
                    "source": selected_source,
                }
            )

        coverage_rows.append(
            {
                "pit_ticker": pit_ticker,
                "membership_start": membership_start.isoformat(),
                "membership_end_exclusive": membership_end_exclusive.isoformat(),
                "interval_count": len(windows),
                "selected_source": selected_source,
                "selected_status": selected_status,
                "selected_rows": len(chosen),
                "tiingo_status": tiingo_status,
                "tiingo_rows": len(tiingo_rows),
                "tiingo_start_gap_days": (
                    "" if tiingo_start_gap is None else tiingo_start_gap
                ),
                "tiingo_end_gap_days": (
                    "" if tiingo_end_gap is None else tiingo_end_gap
                ),
                "stooq_status": stooq_status,
                "stooq_rows": len(stooq_rows),
                "stooq_start_gap_days": (
                    "" if stooq_start_gap is None else stooq_start_gap
                ),
                "stooq_end_gap_days": (
                    "" if stooq_end_gap is None else stooq_end_gap
                ),
                "stooq_market_tickers": "|".join(stooq_symbols),
                "stooq_exclusion_reason": stooq_exclusion or "",
            }
        )

        print(
            f"[{number:03d}/{len(windows_by_ticker):03d}] "
            f"{pit_ticker:6s} selected={selected_source:10s} "
            f"tiingo={tiingo_status:25s} stooq={stooq_status:25s}"
        )

    args.coverage_output.parent.mkdir(parents=True, exist_ok=True)
    with args.coverage_output.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "pit_ticker",
            "membership_start",
            "membership_end_exclusive",
            "interval_count",
            "selected_source",
            "selected_status",
            "selected_rows",
            "tiingo_status",
            "tiingo_rows",
            "tiingo_start_gap_days",
            "tiingo_end_gap_days",
            "stooq_status",
            "stooq_rows",
            "stooq_start_gap_days",
            "stooq_end_gap_days",
            "stooq_market_tickers",
            "stooq_exclusion_reason",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(coverage_rows)

    args.prices_output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(
        args.prices_output,
        "wt",
        encoding="utf-8",
        newline="",
    ) as handle:
        fields = [
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
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected_prices)

    tiingo_selected = sum(
        row["selected_source"] == "tiingo" for row in coverage_rows
    )
    stooq_selected = sum(
        row["selected_source"] == "stooq_bulk" for row in coverage_rows
    )
    unresolved = sum(
        row["selected_source"] == "unresolved" for row in coverage_rows
    )

    print()
    print("CANONICAL MARKET DATA")
    print(f"PIT tickers:       {len(coverage_rows)}")
    print(f"Tiingo selected:   {tiingo_selected}")
    print(f"Stooq selected:    {stooq_selected}")
    print(f"Unresolved:        {unresolved}")
    print(f"Daily price rows:  {len(selected_prices):,}")
    print(f"Coverage manifest: {args.coverage_output}")
    print(f"Price dataset:     {args.prices_output}")


if __name__ == "__main__":
    main()
