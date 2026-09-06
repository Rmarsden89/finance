from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Override:
    ticker: str
    cik: int
    valid_from: date
    valid_to: date | None
    company_name: str
    evidence: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Propose conservative extensions for residual weekly identity gaps. "
            "Only tickers with exactly one known historical CIK are auto-proposed."
        )
    )
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument(
        "--existing-overrides",
        type=Path,
        default=Path("data/reference/historical_identity_overrides.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/proposed_identity_interval_extensions.csv"),
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("reports/residual_identity_review.csv"),
    )
    parser.add_argument(
        "--max-boundary-gap-days",
        type=int,
        default=14,
        help="Only auto-propose when unresolved weeks begin within N days of an override boundary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides = _load_overrides(args.existing_overrides)

    by_ticker: dict[str, list[Override]] = defaultdict(list)
    for row in overrides:
        by_ticker[row.ticker].append(row)

    unresolved_rows: list[dict[str, str]] = []
    with args.panel.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if _truthy(row.get("identity_resolved")):
                continue
            unresolved_rows.append(row)

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in unresolved_rows:
        key = (
            row["ticker"].strip().upper(),
            row.get("membership_start", ""),
            row.get("membership_end", ""),
        )
        grouped[key].append(row)

    proposals: list[dict[str, str]] = []
    review: list[dict[str, str]] = []

    for (ticker, membership_start, membership_end), rows in sorted(grouped.items()):
        ticker_overrides = by_ticker.get(ticker, [])
        known_ciks = sorted({row.cik for row in ticker_overrides})

        first_decision = min(date.fromisoformat(row["decision_date"]) for row in rows)
        last_decision = max(date.fromisoformat(row["decision_date"]) for row in rows)

        if len(known_ciks) != 1:
            review.append(
                {
                    "ticker": ticker,
                    "known_ciks": "|".join(str(cik) for cik in known_ciks),
                    "first_unresolved": first_decision.isoformat(),
                    "last_unresolved": last_decision.isoformat(),
                    "membership_start": membership_start,
                    "membership_end": membership_end,
                    "reason": (
                        "no existing verified override"
                        if not known_ciks
                        else "multiple historical CIKs; manual review required"
                    ),
                }
            )
            continue

        cik = known_ciks[0]
        matching = sorted(
            ticker_overrides,
            key=lambda row: row.valid_from,
        )

        nearest = min(
            matching,
            key=lambda row: _distance_to_interval(first_decision, row),
        )

        boundary_gap = _boundary_gap_days(first_decision, nearest)
        if boundary_gap is None or boundary_gap > args.max_boundary_gap_days:
            review.append(
                {
                    "ticker": ticker,
                    "known_ciks": str(cik),
                    "first_unresolved": first_decision.isoformat(),
                    "last_unresolved": last_decision.isoformat(),
                    "membership_start": membership_start,
                    "membership_end": membership_end,
                    "reason": (
                        f"unresolved interval is {boundary_gap if boundary_gap is not None else 'not'} "
                        "days from verified override boundary; manual review required"
                    ),
                }
            )
            continue

        proposed_from = nearest.valid_from
        proposed_to = max(
            nearest.valid_to or (last_decision + timedelta(days=1)),
            last_decision + timedelta(days=7),
        )

        proposals.append(
            {
                "ticker": ticker,
                "cik": str(cik),
                "existing_valid_from": nearest.valid_from.isoformat(),
                "existing_valid_to": (
                    nearest.valid_to.isoformat() if nearest.valid_to else ""
                ),
                "proposed_valid_from": proposed_from.isoformat(),
                "proposed_valid_to": (
                    proposed_to.isoformat() if proposed_to else ""
                ),
                "first_unresolved": first_decision.isoformat(),
                "last_unresolved": last_decision.isoformat(),
                "membership_start": membership_start,
                "membership_end": membership_end,
                "company_name": nearest.company_name,
                "reason": (
                    "single known historical CIK; unresolved interval begins near "
                    "verified boundary; extend only through observed unresolved weeks"
                ),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write(
        args.output,
        proposals,
        [
            "ticker",
            "cik",
            "existing_valid_from",
            "existing_valid_to",
            "proposed_valid_from",
            "proposed_valid_to",
            "first_unresolved",
            "last_unresolved",
            "membership_start",
            "membership_end",
            "company_name",
            "reason",
        ],
    )
    _write(
        args.review_output,
        review,
        [
            "ticker",
            "known_ciks",
            "first_unresolved",
            "last_unresolved",
            "membership_start",
            "membership_end",
            "reason",
        ],
    )

    print("RESIDUAL IDENTITY INTERVAL AUDIT")
    print(f"Unresolved panel rows:       {len(unresolved_rows):,}")
    print(f"Residual membership groups: {len(grouped):,}")
    print(f"Safe extension proposals:   {len(proposals):,}")
    print(f"Held for review:            {len(review):,}")
    print(f"Proposals:                  {args.output}")
    print(f"Review:                     {args.review_output}")


def _load_overrides(path: Path) -> list[Override]:
    rows: list[Override] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                Override(
                    ticker=row["ticker"].strip().upper(),
                    cik=int(row["cik"]),
                    valid_from=date.fromisoformat(row["valid_from"]),
                    valid_to=(
                        date.fromisoformat(row["valid_to"])
                        if row.get("valid_to")
                        else None
                    ),
                    company_name=(row.get("company_name") or "").strip(),
                    evidence=(row.get("evidence") or "").strip(),
                )
            )
    return rows


def _distance_to_interval(value: date, row: Override) -> int:
    if value < row.valid_from:
        return (row.valid_from - value).days
    if row.valid_to is None or value < row.valid_to:
        return 0
    return (value - row.valid_to).days


def _boundary_gap_days(value: date, row: Override) -> int | None:
    if row.valid_to is not None and value >= row.valid_to:
        return (value - row.valid_to).days
    if value < row.valid_from:
        return (row.valid_from - value).days
    return 0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _write(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
