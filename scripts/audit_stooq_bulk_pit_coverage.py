from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.stooq import StooqBulkArchive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the Stooq US daily bulk ZIP against PIT S&P 500 membership. "
            "Optionally restrict to Tiingo gaps."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
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

    selected = sorted(windows)
    if args.tiingo_report:
        wanted = tiingo_gaps(args.tiingo_report)
        selected = [ticker for ticker in selected if ticker in wanted]

    archive = StooqBulkArchive(args.archive)
    archive_symbols = archive.symbols()

    print("STOOQ BULK PIT COVERAGE")
    print(f"Archive symbols indexed: {len(archive_symbols):,}")
    print(f"Tickers selected:        {len(selected):,}")
    print()

    rows = []
    for number, ticker in enumerate(selected, start=1):
        ticker_windows = windows[ticker]
        request_start = min(start for start, _ in ticker_windows)
        request_end = max(end for _, end in ticker_windows) - timedelta(days=1)

        result = archive.coverage(
            ticker,
            start=request_start,
            end=request_end,
        )
        status, start_gap, end_gap = classify(
            windows=ticker_windows,
            first_price=result.first_price_date,
            last_price=result.last_price_date,
            tolerance_days=args.boundary_tolerance_days,
            error=result.error,
        )

        rows.append(
            {
                "ticker": ticker,
                "archive_symbol_present": ticker in archive_symbols,
                "membership_start": request_start,
                "membership_end_exclusive": request_end + timedelta(days=1),
                "price_start": result.first_price_date or "",
                "price_end": result.last_price_date or "",
                "rows": result.rows,
                "start_gap_days": "" if start_gap is None else start_gap,
                "end_gap_days": "" if end_gap is None else end_gap,
                "status": status,
                "error": result.error or "",
            }
        )

        print(
            f"[{number:03d}/{len(selected):03d}] {ticker:6s} "
            f"{status:25s} "
            f"{result.first_price_date or '-'} -> {result.last_price_date or '-'}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "archive_symbol_present",
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

    print()
    print("SUMMARY")
    print(f"Full boundary:    {full}")
    print(f"Partial boundary: {partial}")
    print(f"Missing:          {missing}")
    print(f"Provider errors:  {errors}")
    print(f"Report:           {args.output}")


if __name__ == "__main__":
    main()
