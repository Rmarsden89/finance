from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a Tiingo recovery priority queue from canonical market coverage. "
            "Unresolved names are always attempted before already-covered fallbacks."
        )
    )
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--twelve-data-report",
        type=Path,
        help="Optional strict Twelve Data coverage report.",
    )
    parser.add_argument(
        "--twelve-data-verified",
        type=Path,
        default=Path("data/reference/twelve_data_verified_resolutions.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/tiingo_priority_queue.csv"),
    )
    return parser.parse_args()


def load_rows(path: Path, key: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            (row.get(key) or "").strip().upper(): row
            for row in csv.DictReader(handle)
            if (row.get(key) or "").strip()
        }


def verified_twelve(path: Path) -> set[str]:
    rows = load_rows(path, "pit_ticker")
    return {
        ticker
        for ticker, row in rows.items()
        if (row.get("verification_status") or "").strip().lower()
        == "verified_full_coverage"
    }


def classify_priority(
    coverage: dict[str, str],
    twelve: dict[str, str] | None,
    twelve_verified: bool,
) -> tuple[int, str]:
    selected_source = (coverage.get("selected_source") or "").strip()
    selected_status = (coverage.get("selected_status") or "").strip()
    stooq_status = (coverage.get("stooq_status") or "").strip()

    twelve_status = ""
    if twelve:
        twelve_status = (twelve.get("status") or "").strip()

    if selected_source == "unresolved":
        if stooq_status == "partial_boundary_coverage":
            return 2, "canonical_unresolved_stooq_partial"
        return 1, "canonical_unresolved_no_full_fallback"

    if twelve_verified or twelve_status in {
        "full_boundary_coverage",
        "partial_boundary_coverage",
    }:
        return 3, "twelve_data_covered_validate_with_tiingo"

    if selected_source == "stooq_bulk" and selected_status == "full_boundary_coverage":
        return 4, "stooq_full_replace_if_tiingo_available"

    return 9, "already_tiingo_or_not_needed"


def main() -> None:
    args = parse_args()

    coverage = load_rows(args.coverage, "pit_ticker")
    twelve = (
        load_rows(args.twelve_data_report, "ticker")
        if args.twelve_data_report
        else {}
    )
    verified = verified_twelve(args.twelve_data_verified)

    rows = []
    for ticker, cov in coverage.items():
        priority, reason = classify_priority(
            cov,
            twelve.get(ticker),
            ticker in verified,
        )
        if priority == 9:
            continue

        td = twelve.get(ticker, {})
        rows.append(
            {
                "priority": priority,
                "pit_ticker": ticker,
                "reason": reason,
                "canonical_source": (cov.get("selected_source") or "").strip(),
                "canonical_status": (cov.get("selected_status") or "").strip(),
                "tiingo_status": (cov.get("tiingo_status") or "").strip(),
                "stooq_status": (cov.get("stooq_status") or "").strip(),
                "twelve_data_status": (td.get("status") or "").strip(),
                "twelve_data_verified": ticker in verified,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), row["pit_ticker"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "priority",
        "pit_ticker",
        "reason",
        "canonical_source",
        "canonical_status",
        "tiingo_status",
        "stooq_status",
        "twelve_data_status",
        "twelve_data_verified",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("TIINGO PRIORITY QUEUE")
    print(f"Queue rows: {len(rows)}")
    for priority in (1, 2, 3, 4):
        count = sum(int(row["priority"]) == priority for row in rows)
        print(f"Priority {priority}: {count}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
