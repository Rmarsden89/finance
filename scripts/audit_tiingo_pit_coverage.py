from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.tiingo import TiingoClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Tiingo price coverage against PIT S&P 500 membership windows."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--token", help="Tiingo token; defaults to TIINGO_API_TOKEN.")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--boundary-tolerance-days",
        type=int,
        default=7,
        help="Calendar-day tolerance for weekends/holidays at membership boundaries.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/tiingo_pit_price_coverage.csv"),
    )
    return parser.parse_args()


def _membership_windows(intervals, *, start: date, end: date):
    windows = defaultdict(list)
    audit_end_exclusive = end + timedelta(days=1)
    for interval in intervals:
        interval_end = interval.end_date or audit_end_exclusive
        window_start = max(interval.start_date, start)
        window_end = min(interval_end, audit_end_exclusive)
        if window_start < window_end:
            windows[interval.ticker].append((window_start, window_end))
    return dict(windows)


def _classify(
    *,
    windows: list[tuple[date, date]],
    first_price: date | None,
    last_price: date | None,
    tolerance_days: int,
) -> tuple[str, int | None, int | None]:
    if first_price is None or last_price is None:
        return "missing", None, None

    membership_start = min(start for start, _ in windows)
    membership_end_inclusive = max(end for _, end in windows) - timedelta(days=1)
    start_gap = (first_price - membership_start).days
    end_gap = (membership_end_inclusive - last_price).days

    start_ok = start_gap <= tolerance_days
    end_ok = end_gap <= tolerance_days
    if start_ok and end_ok:
        return "full_boundary_coverage", start_gap, end_gap
    return "partial_boundary_coverage", start_gap, end_gap


def main() -> None:
    args = parse_args()
    token = args.token or os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise SystemExit("Tiingo token required. Pass --token or set TIINGO_API_TOKEN.")

    audit_start = date(args.start_year, 1, 1)
    audit_end = date(args.end_year, 12, 31)
    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = _membership_windows(intervals, start=audit_start, end=audit_end)
    tickers = sorted(windows)

    start_index = args.offset
    end_index = None if args.limit is None else args.offset + args.limit
    selected = tickers[start_index:end_index]

    client = TiingoClient(token)
    rows = []
    for number, ticker in enumerate(selected, start=1):
        ticker_windows = windows[ticker]
        request_start = min(start for start, _ in ticker_windows)
        request_end = max(end for _, end in ticker_windows) - timedelta(days=1)
        result = client.coverage(ticker, start=request_start, end=request_end)
        status, start_gap, end_gap = _classify(
            windows=ticker_windows,
            first_price=result.first_price_date,
            last_price=result.last_price_date,
            tolerance_days=args.boundary_tolerance_days,
        )

        rows.append({
            "ticker": ticker,
            "interval_count": len(ticker_windows),
            "membership_start": request_start,
            "membership_end_exclusive": request_end + timedelta(days=1),
            "price_start": result.first_price_date or "",
            "price_end": result.last_price_date or "",
            "rows": result.rows,
            "start_gap_days": "" if start_gap is None else start_gap,
            "end_gap_days": "" if end_gap is None else end_gap,
            "status": status,
            "error": result.error or "",
        })

        print(
            f"[{number:03d}/{len(selected):03d}] {ticker:6s} {status:25s} "
            f"{request_start} -> {request_end} | "
            f"{result.first_price_date or '-'} -> {result.last_price_date or '-'}"
        )
        if result.error:
            print(f"             error={result.error}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ticker", "interval_count", "membership_start", "membership_end_exclusive",
        "price_start", "price_end", "rows", "start_gap_days", "end_gap_days",
        "status", "error"
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    full = sum(row["status"] == "full_boundary_coverage" for row in rows)
    partial = sum(row["status"] == "partial_boundary_coverage" for row in rows)
    missing = sum(row["status"] == "missing" for row in rows)
    print()
    print("TIINGO PIT MEMBERSHIP COVERAGE")
    print(f"Universe tickers: {len(tickers)}")
    print(f"Batch offset:     {args.offset}")
    print(f"Batch tested:     {len(rows)}")
    print(f"Full boundary:    {full}")
    print(f"Partial boundary: {partial}")
    print(f"Missing:          {missing}")
    print(f"Report:           {args.output}")


if __name__ == "__main__":
    main()
