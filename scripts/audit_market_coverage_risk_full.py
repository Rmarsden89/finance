from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.sources.pitindex import load_pitindex_sp500


RISK_STATUSES = {
    "partial_boundary_coverage",
    "missing",
    "provider_error",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit canonical market-data coverage risk across the full PIT "
            "S&P 500 universe using membership-days and yearly exposure."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/market_coverage_risk_full.csv"),
    )
    return parser.parse_args()


def load_coverage(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            (row.get("pit_ticker") or "").strip().upper(): row
            for row in csv.DictReader(handle)
            if (row.get("pit_ticker") or "").strip()
        }


def membership_days_by_ticker_and_year(
    intervals,
    *,
    start: date,
    end: date,
):
    end_exclusive = end + timedelta(days=1)
    total_days = defaultdict(int)
    yearly_days = defaultdict(lambda: defaultdict(int))

    for interval in intervals:
        ticker = interval.ticker.upper()
        interval_end = interval.end_date or end_exclusive
        left = max(interval.start_date, start)
        right = min(interval_end, end_exclusive)
        if left >= right:
            continue

        total_days[ticker] += (right - left).days

        for year in range(left.year, right.year + 1):
            year_left = max(left, date(year, 1, 1))
            year_right = min(right, date(year + 1, 1, 1))
            if year_left < year_right:
                yearly_days[ticker][year] += (year_right - year_left).days

    return total_days, yearly_days


def main() -> None:
    args = parse_args()
    start = date(args.start_year, 1, 1)
    end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    coverage = load_coverage(args.coverage)
    membership_days, yearly_days = membership_days_by_ticker_and_year(
        intervals,
        start=start,
        end=end,
    )

    all_tickers = sorted(membership_days)
    detail_rows = []

    source_counts = Counter()
    status_counts = Counter()
    source_days = Counter()
    status_days = Counter()
    total_year_days = Counter()
    risk_year_days = Counter()

    for ticker in all_tickers:
        row = coverage.get(ticker, {})
        source = (row.get("selected_source") or "missing_manifest").strip()
        status = (row.get("selected_status") or "missing_manifest").strip()
        days = membership_days[ticker]

        source_counts[source] += 1
        status_counts[status] += 1
        source_days[source] += days
        status_days[status] += days

        for year, year_days in yearly_days[ticker].items():
            total_year_days[year] += year_days
            if source == "unresolved" or status in RISK_STATUSES:
                risk_year_days[year] += year_days

        detail_rows.append(
            {
                "pit_ticker": ticker,
                "membership_days": days,
                "selected_source": source,
                "selected_status": status,
                "selected_rows": row.get("selected_rows") or "",
                "tiingo_status": row.get("tiingo_status") or "",
                "tiingo_rows": row.get("tiingo_rows") or "",
                "stooq_status": row.get("stooq_status") or "",
                "stooq_rows": row.get("stooq_rows") or "",
                "stooq_market_tickers": row.get("stooq_market_tickers") or "",
                "stooq_exclusion_reason": row.get("stooq_exclusion_reason") or "",
            }
        )

    detail_rows.sort(
        key=lambda row: (
            0
            if row["selected_source"] == "unresolved"
            else 1,
            -int(row["membership_days"]),
            row["pit_ticker"],
        )
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pit_ticker",
        "membership_days",
        "selected_source",
        "selected_status",
        "selected_rows",
        "tiingo_status",
        "tiingo_rows",
        "stooq_status",
        "stooq_rows",
        "stooq_market_tickers",
        "stooq_exclusion_reason",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(detail_rows)

    total_days = sum(membership_days.values())
    unresolved_days = source_days["unresolved"]
    covered_days = total_days - unresolved_days

    print("FULL PIT MARKET COVERAGE RISK AUDIT")
    print(f"Period:                  {args.start_year}-{args.end_year}")
    print(f"PIT tickers:             {len(all_tickers):,}")
    print(f"Coverage manifest rows:  {len(coverage):,}")
    print()
    print("SELECTED SOURCES — TICKERS")
    for source, count in source_counts.most_common():
        print(f"{source:28s} {count:6,d}")
    print()
    print("SELECTED STATUS — TICKERS")
    for status, count in status_counts.most_common():
        print(f"{status:28s} {count:6,d}")
    print()
    print("MEMBERSHIP-DAY COVERAGE")
    print(f"Total membership-days:   {total_days:,}")
    print(f"Covered membership-days: {covered_days:,}")
    print(f"Unresolved days:         {unresolved_days:,}")
    if total_days:
        print(f"Covered share:           {100.0 * covered_days / total_days:.3f}%")
        print(f"Unresolved share:        {100.0 * unresolved_days / total_days:.3f}%")
    print()
    print("MEMBERSHIP-DAYS BY SOURCE")
    for source, days in source_days.most_common():
        share = 100.0 * days / total_days if total_days else 0.0
        print(f"{source:28s} {days:10,d}  {share:7.3f}%")
    print()
    print("UNRESOLVED SHARE BY YEAR")
    for year in range(args.start_year, args.end_year + 1):
        total = total_year_days[year]
        risk = risk_year_days[year]
        share = 100.0 * risk / total if total else 0.0
        print(f"{year}: {risk:7,d} / {total:7,d}  {share:7.3f}%")
    print()
    print("LARGEST UNRESOLVED MEMBERSHIP EXPOSURES")
    unresolved = [
        row
        for row in detail_rows
        if row["selected_source"] == "unresolved"
    ]
    for row in unresolved[:20]:
        print(
            f"{row['pit_ticker']:6s} "
            f"days={int(row['membership_days']):5d} "
            f"status={row['selected_status']:25s} "
            f"tiingo={row['tiingo_status'] or '-':25s} "
            f"stooq={row['stooq_status'] or '-':25s}"
        )

    print()
    print(f"Detail report: {args.output}")


if __name__ == "__main__":
    main()
