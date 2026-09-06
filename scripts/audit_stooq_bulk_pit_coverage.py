from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.historical_market_tickers import (
    HistoricalMarketTickerOverride,
    load_historical_market_ticker_overrides,
    market_ticker_as_of,
)
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.stooq import StooqBulkArchive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the Stooq US daily bulk ZIP against PIT S&P 500 membership. "
            "Historical market-ticker overrides are applied segment-by-segment."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument(
        "--tiingo-report",
        type=Path,
        help=(
            "Optional cumulative Tiingo coverage CSV. When supplied, only "
            "tickers not marked full_boundary_coverage are tested."
        ),
    )
    parser.add_argument(
        "--boundary-tolerance-days",
        type=int,
        default=7,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/stooq_bulk_pit_coverage.csv"),
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


def tiingo_gaps(path: Path) -> set[str]:
    gaps: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("ticker") or "").strip().upper()
            status = (row.get("status") or "").strip()
            if ticker and status != "full_boundary_coverage":
                gaps.add(ticker)
    return gaps


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
    segments: list[tuple[date, date, str]] = []
    for left, right in zip(ordered, ordered[1:]):
        if left >= right:
            continue
        market_ticker = market_ticker_as_of(
            overrides,
            pit_ticker=pit_ticker,
            as_of=left,
        )
        segments.append((left, right, market_ticker))
    return segments


def classify(
    *,
    windows,
    first_price,
    last_price,
    tolerance_days: int,
    error: str | None,
):
    if error:
        return "provider_error", None, None
    if first_price is None or last_price is None:
        return "missing", None, None

    membership_start = min(start for start, _ in windows)
    membership_end = max(end for _, end in windows) - timedelta(days=1)
    start_gap = (first_price - membership_start).days
    end_gap = (membership_end - last_price).days

    if start_gap <= tolerance_days and end_gap <= tolerance_days:
        return "full_boundary_coverage", start_gap, end_gap
    return "partial_boundary_coverage", start_gap, end_gap


def main() -> None:
    args = parse_args()
    audit_start = date(args.start_year, 1, 1)
    audit_end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = membership_windows(intervals, start=audit_start, end=audit_end)
    overrides = load_historical_market_ticker_overrides(
        args.historical_market_tickers
    )

    selected = sorted(windows)
    if args.tiingo_report:
        wanted = tiingo_gaps(args.tiingo_report)
        selected = [ticker for ticker in selected if ticker in wanted]

    archive = StooqBulkArchive(args.archive)
    archive_symbols = archive.symbols()

    print("STOOQ BULK PIT COVERAGE — HISTORICAL TICKER AWARE")
    print(f"Archive symbols indexed: {len(archive_symbols):,}")
    print(f"Tickers selected:        {len(selected):,}")
    print(f"Ticker overrides loaded: {len(overrides):,}")
    print()

    rows = []
    for number, ticker in enumerate(selected, start=1):
        ticker_windows = windows[ticker]
        all_prices = {}
        market_tickers_used: list[str] = []
        segment_descriptions: list[str] = []
        errors: list[str] = []

        for window_start, window_end in ticker_windows:
            segments = split_market_segments(
                pit_ticker=ticker,
                start=window_start,
                end_exclusive=window_end,
                overrides=overrides,
            )
            for segment_start, segment_end, market_ticker in segments:
                if market_ticker not in market_tickers_used:
                    market_tickers_used.append(market_ticker)

                segment_descriptions.append(
                    f"{segment_start}:{segment_end}:{market_ticker}"
                )

                result = archive.coverage(
                    market_ticker,
                    start=segment_start,
                    end=segment_end - timedelta(days=1),
                )
                if result.error:
                    errors.append(
                        f"{market_ticker} {segment_start}->{segment_end}: "
                        f"{result.error}"
                    )
                    continue

                prices = archive.daily_prices(
                    market_ticker,
                    start=segment_start,
                    end=segment_end - timedelta(days=1),
                )
                for price in prices:
                    all_prices[price.date] = price

        ordered_dates = sorted(all_prices)
        first_price = ordered_dates[0] if ordered_dates else None
        last_price = ordered_dates[-1] if ordered_dates else None
        error = " | ".join(errors) if errors else None

        status, start_gap, end_gap = classify(
            windows=ticker_windows,
            first_price=first_price,
            last_price=last_price,
            tolerance_days=args.boundary_tolerance_days,
            error=error,
        )

        request_start = min(start for start, _ in ticker_windows)
        request_end_exclusive = max(end for _, end in ticker_windows)

        rows.append(
            {
                "ticker": ticker,
                "direct_archive_symbol_present": ticker in archive_symbols,
                "market_tickers_used": "|".join(market_tickers_used),
                "market_symbols_present": "|".join(
                    f"{symbol}:{symbol in archive_symbols}"
                    for symbol in market_tickers_used
                ),
                "segments": "|".join(segment_descriptions),
                "membership_start": request_start,
                "membership_end_exclusive": request_end_exclusive,
                "price_start": first_price or "",
                "price_end": last_price or "",
                "rows": len(all_prices),
                "start_gap_days": "" if start_gap is None else start_gap,
                "end_gap_days": "" if end_gap is None else end_gap,
                "status": status,
                "error": error or "",
            }
        )

        mapping = (
            ticker
            if market_tickers_used == [ticker]
            else " -> " + ",".join(market_tickers_used)
        )
        print(
            f"[{number:03d}/{len(selected):03d}] {ticker:6s} "
            f"{status:25s} {mapping:18s} "
            f"{first_price or '-'} -> {last_price or '-'}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "direct_archive_symbol_present",
        "market_tickers_used",
        "market_symbols_present",
        "segments",
        "membership_start",
        "membership_end_exclusive",
        "price_start",
        "price_end",
        "rows",
        "start_gap_days",
        "end_gap_days",
        "status",
        "error",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    full = sum(row["status"] == "full_boundary_coverage" for row in rows)
    partial = sum(row["status"] == "partial_boundary_coverage" for row in rows)
    missing = sum(row["status"] == "missing" for row in rows)
    errors = sum(row["status"] == "provider_error" for row in rows)
    reconciled = sum(
        row["market_tickers_used"] != row["ticker"]
        for row in rows
    )

    print()
    print("SUMMARY")
    print(f"Full boundary:          {full}")
    print(f"Partial boundary:       {partial}")
    print(f"Missing:                {missing}")
    print(f"Provider errors:        {errors}")
    print(f"Historical mappings:    {reconciled}")
    print(f"Report:                 {args.output}")


if __name__ == "__main__":
    main()
