from __future__ import annotations

import argparse
import csv
import gzip
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.sources.pitindex import load_pitindex_sp500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate canonical PIT market-price outputs."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
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


def main() -> None:
    args = parse_args()
    audit_start = date(args.start_year, 1, 1)
    audit_end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = membership_windows(intervals, start=audit_start, end=audit_end)

    coverage_by_ticker = {}
    with args.coverage.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = row["pit_ticker"].strip().upper()
            coverage_by_ticker[ticker] = row

    duplicate_keys = []
    outside_membership = []
    invalid_prices = []
    source_mismatches = []
    unresolved_with_prices = []
    bad_order = []
    rows_by_ticker = Counter()
    source_counts = Counter()
    seen = set()
    last_date_by_ticker = {}

    opener = gzip.open if args.prices.suffix == ".gz" else open
    with opener(args.prices, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
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
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise SystemExit(
                f"Unexpected price schema: {reader.fieldnames}"
            )

        for line_number, row in enumerate(reader, start=2):
            ticker = row["pit_ticker"].strip().upper()
            source = row["source"].strip()
            try:
                price_date = date.fromisoformat(row["date"])
            except ValueError:
                invalid_prices.append((line_number, ticker, "invalid_date"))
                continue

            key = (ticker, price_date)
            if key in seen:
                duplicate_keys.append(key)
            else:
                seen.add(key)

            prior = last_date_by_ticker.get(ticker)
            if prior is not None and price_date < prior:
                bad_order.append((ticker, prior, price_date))
            last_date_by_ticker[ticker] = price_date

            ticker_windows = windows.get(ticker, [])
            if not any(start <= price_date < end for start, end in ticker_windows):
                outside_membership.append((ticker, price_date))

            for field in ("open", "high", "low", "close"):
                try:
                    value = float(row[field])
                    if value <= 0:
                        invalid_prices.append(
                            (line_number, ticker, f"{field}_nonpositive")
                        )
                except (TypeError, ValueError):
                    invalid_prices.append(
                        (line_number, ticker, f"{field}_invalid")
                    )

            coverage = coverage_by_ticker.get(ticker)
            if coverage is None:
                source_mismatches.append(
                    (ticker, source, "missing_coverage_row")
                )
            else:
                selected_source = coverage["selected_source"].strip()
                if selected_source != source:
                    source_mismatches.append(
                        (ticker, source, selected_source)
                    )
                if selected_source == "unresolved":
                    unresolved_with_prices.append(ticker)

            rows_by_ticker[ticker] += 1
            source_counts[source] += 1

    selected_without_prices = []
    unresolved_count = 0
    selected_source_counts = Counter()

    for ticker, row in coverage_by_ticker.items():
        selected_source = row["selected_source"].strip()
        selected_source_counts[selected_source] += 1
        if selected_source == "unresolved":
            unresolved_count += 1
            continue
        if rows_by_ticker[ticker] == 0:
            selected_without_prices.append(ticker)

    expected_tickers = set(windows)
    missing_coverage_rows = sorted(expected_tickers - set(coverage_by_ticker))
    extra_coverage_rows = sorted(set(coverage_by_ticker) - expected_tickers)

    errors = (
        len(duplicate_keys)
        + len(outside_membership)
        + len(invalid_prices)
        + len(source_mismatches)
        + len(unresolved_with_prices)
        + len(bad_order)
        + len(selected_without_prices)
        + len(missing_coverage_rows)
        + len(extra_coverage_rows)
    )

    print("CANONICAL MARKET DATA VALIDATION")
    print(f"Coverage rows:                 {len(coverage_by_ticker):,}")
    print(f"Materialized price rows:       {sum(rows_by_ticker.values()):,}")
    print(f"Tickers with price rows:       {len(rows_by_ticker):,}")
    print()
    print("SELECTED SOURCES")
    for source, count in sorted(selected_source_counts.items()):
        print(f"{source:20s} {count:6d}")
    print()
    print("MATERIALIZED PRICE ROWS")
    for source, count in sorted(source_counts.items()):
        print(f"{source:20s} {count:10,d}")
    print()
    print("VALIDATION CHECKS")
    print(f"Duplicate ticker+date:         {len(duplicate_keys):,}")
    print(f"Outside membership windows:   {len(outside_membership):,}")
    print(f"Invalid/nonpositive prices:   {len(invalid_prices):,}")
    print(f"Source mismatches:             {len(source_mismatches):,}")
    print(f"Unresolved with price rows:   {len(set(unresolved_with_prices)):,}")
    print(f"Out-of-order dates:           {len(bad_order):,}")
    print(f"Selected with zero rows:      {len(selected_without_prices):,}")
    print(f"Missing coverage rows:        {len(missing_coverage_rows):,}")
    print(f"Extra coverage rows:          {len(extra_coverage_rows):,}")
    print()

    if duplicate_keys[:10]:
        print("Duplicate examples:", duplicate_keys[:10])
    if outside_membership[:10]:
        print("Outside membership examples:", outside_membership[:10])
    if invalid_prices[:10]:
        print("Invalid price examples:", invalid_prices[:10])
    if source_mismatches[:10]:
        print("Source mismatch examples:", source_mismatches[:10])
    if bad_order[:10]:
        print("Ordering examples:", bad_order[:10])
    if selected_without_prices[:20]:
        print("Selected with zero rows:", selected_without_prices[:20])
    if missing_coverage_rows[:20]:
        print("Missing coverage rows:", missing_coverage_rows[:20])
    if extra_coverage_rows[:20]:
        print("Extra coverage rows:", extra_coverage_rows[:20])

    print()
    if errors:
        print(f"RESULT: FAIL ({errors:,} validation issues)")
        raise SystemExit(1)

    print("RESULT: PASS")


if __name__ == "__main__":
    main()
