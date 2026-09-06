from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from finance.data.sources.pitindex import load_pitindex_sp500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure current market-data coverage risk for the attempted portion "
            "of the Tiingo priority queue."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--priority-queue",
        type=Path,
        default=Path("reports/tiingo_priority_queue.csv"),
    )
    parser.add_argument(
        "--tiingo-report",
        type=Path,
        default=Path("reports/tiingo_priority_coverage.csv"),
    )
    parser.add_argument(
        "--cutoff-ticker",
        default="SBNY",
        help="Only review queue rows before this ticker. Cutoff itself is excluded.",
    )
    parser.add_argument(
        "--max-priority",
        type=int,
        default=2,
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/market_coverage_risk_current.csv"),
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def clipped_membership_days(
    intervals,
    wanted: set[str],
    *,
    start: date,
    end: date,
):
    end_exclusive = date(end.year + 1, 1, 1)
    total_days = defaultdict(int)
    yearly_days = defaultdict(lambda: defaultdict(int))

    for interval in intervals:
        ticker = interval.ticker.upper()
        if ticker not in wanted:
            continue

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
    queue = [
        row
        for row in read_csv(args.priority_queue)
        if int(row["priority"]) <= args.max_priority
    ]
    queue.sort(key=lambda row: (int(row["priority"]), row["pit_ticker"].upper()))

    cutoff = args.cutoff_ticker.upper()
    before_cutoff = []
    found_cutoff = False
    for row in queue:
        ticker = row["pit_ticker"].upper()
        if ticker == cutoff:
            found_cutoff = True
            break
        before_cutoff.append(row)

    if not found_cutoff:
        print(
            f"WARNING: cutoff ticker {cutoff} not found in selected queue; "
            "using all queue rows."
        )

    report_rows = {
        row["pit_ticker"].upper(): row
        for row in read_csv(args.tiingo_report)
        if row.get("pit_ticker")
    }

    attempted = [
        row
        for row in before_cutoff
        if row["pit_ticker"].upper() in report_rows
    ]
    attempted_tickers = {row["pit_ticker"].upper() for row in attempted}

    intervals = load_pitindex_sp500(args.pitindex_data)
    total_days, yearly_days = clipped_membership_days(
        intervals,
        attempted_tickers,
        start=date(args.start_year, 1, 1),
        end=date(args.end_year, 12, 31),
    )

    detail_rows = []
    status_counts = Counter()
    status_days = Counter()
    risk_year_days = Counter()

    for queue_row in attempted:
        ticker = queue_row["pit_ticker"].upper()
        report = report_rows[ticker]
        status = (report.get("status") or "").strip()
        membership_days = total_days.get(ticker, 0)

        status_counts[status] += 1
        status_days[status] += membership_days

        at_risk = status in {"missing", "partial_boundary_coverage", "provider_error"}
        if at_risk:
            for year, days in yearly_days.get(ticker, {}).items():
                risk_year_days[year] += days

        detail_rows.append(
            {
                "priority": queue_row["priority"],
                "pit_ticker": ticker,
                "status": status,
                "membership_days": membership_days,
                "price_start": report.get("price_start") or "",
                "price_end": report.get("price_end") or "",
                "start_gap_days": report.get("start_gap_days") or "",
                "end_gap_days": report.get("end_gap_days") or "",
                "reason": queue_row.get("reason") or "",
            }
        )

    detail_rows.sort(
        key=lambda row: (
            0 if row["status"] == "missing" else
            1 if row["status"] == "partial_boundary_coverage" else
            2,
            -int(row["membership_days"]),
            row["pit_ticker"],
        )
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "priority",
        "pit_ticker",
        "status",
        "membership_days",
        "price_start",
        "price_end",
        "start_gap_days",
        "end_gap_days",
        "reason",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(detail_rows)

    attempted_days = sum(total_days.values())
    covered_days = status_days["full_boundary_coverage"]
    partial_days = status_days["partial_boundary_coverage"]
    missing_days = status_days["missing"]
    error_days = status_days["provider_error"]
    at_risk_days = partial_days + missing_days + error_days

    print("CURRENT MARKET COVERAGE RISK")
    print(f"Queue cutoff:              before {cutoff}")
    print(f"Priorities reviewed:       1-{args.max_priority}")
    print(f"Queue rows before cutoff:  {len(before_cutoff)}")
    print(f"Actually attempted:        {len(attempted)}")
    print()
    print("ATTEMPTED TICKERS")
    for status in (
        "full_boundary_coverage",
        "partial_boundary_coverage",
        "missing",
        "provider_error",
    ):
        print(f"{status:28s} {status_counts[status]:5d}")
    print()
    print("MEMBERSHIP-DAY EXPOSURE")
    print(f"Attempted membership-days: {attempted_days:,}")
    print(f"Full-covered days:         {covered_days:,}")
    print(f"Partial-covered days:      {partial_days:,}")
    print(f"Missing days:              {missing_days:,}")
    print(f"Provider-error days:       {error_days:,}")
    print(f"At-risk days:              {at_risk_days:,}")
    if attempted_days:
        print(
            f"At-risk share:             "
            f"{100.0 * at_risk_days / attempted_days:.2f}%"
        )

    print()
    print("AT-RISK MEMBERSHIP-DAYS BY YEAR")
    for year in range(args.start_year, args.end_year + 1):
        print(f"{year}: {risk_year_days[year]:,}")

    print()
    print("LARGEST CURRENT GAPS")
    risky = [
        row
        for row in detail_rows
        if row["status"] in {
            "missing",
            "partial_boundary_coverage",
            "provider_error",
        }
    ]
    for row in risky[:15]:
        print(
            f"{row['pit_ticker']:6s} "
            f"{row['status']:25s} "
            f"membership_days={int(row['membership_days']):5d} "
            f"price={row['price_start'] or '-'}->{row['price_end'] or '-'}"
        )

    print()
    print(f"Detail report: {args.output}")


if __name__ == "__main__":
    main()
