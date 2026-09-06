from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class Row:
    ticker: str
    cik: int
    valid_from: date
    valid_to: date | None
    company_name: str
    evidence: str
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a proposed consolidated historical identity override file "
            "from existing overrides plus SEC-backed yearly candidate reports."
        )
    )
    parser.add_argument(
        "--existing-overrides",
        type=Path,
        default=Path("data/reference/historical_identity_overrides.csv"),
    )
    parser.add_argument(
        "--candidates-dir",
        type=Path,
        required=True,
        help="Directory containing historical_identity_candidates_YYYY.csv files.",
    )
    parser.add_argument(
        "--min-evidence-count",
        type=int,
        default=2,
        help="Minimum independent SEC instance hits required for auto-promotion.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/proposed_historical_identity_overrides.csv"),
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("reports/historical_identity_override_review.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    rows = _load_existing(args.existing_overrides)
    review: list[dict[str, str]] = []

    candidate_files = sorted(
        args.candidates_dir.glob("historical_identity_candidates_*.csv")
    )

    promoted = 0
    skipped = 0

    for path in candidate_files:
        year = _year_from_filename(path)
        if year is None:
            continue

        with path.open("r", encoding="utf-8", newline="") as handle:
            for candidate in csv.DictReader(handle):
                method = (candidate.get("method") or "").strip()
                score = _float(candidate.get("score"))
                evidence_count = _int(candidate.get("evidence_count"))

                if (
                    method != "sec_instance_ticker"
                    or score != 1.0
                    or evidence_count < args.min_evidence_count
                ):
                    skipped += 1
                    review.append(
                        {
                            "ticker": candidate.get("ticker", ""),
                            "candidate_cik": candidate.get("candidate_cik", ""),
                            "year": str(year),
                            "reason": (
                                f"not auto-promoted: method={method}, "
                                f"score={score}, evidence_count={evidence_count}"
                            ),
                            "sec_name": candidate.get("sec_name", ""),
                            "source_file": path.name,
                        }
                    )
                    continue

                rows.append(
                    Row(
                        ticker=candidate["ticker"].strip().upper(),
                        cik=int(candidate["candidate_cik"]),
                        valid_from=date(year, 1, 1),
                        valid_to=date(year + 1, 1, 1),
                        company_name=(candidate.get("sec_name") or "").strip(),
                        evidence=(
                            f"SEC instance ticker evidence "
                            f"({evidence_count} filings) through {year}-12-15"
                        ),
                        source=path.name,
                    )
                )
                promoted += 1

    consolidated, conflicts = _consolidate(rows)

    for conflict in conflicts:
        review.append(conflict)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "cik",
                "valid_from",
                "valid_to",
                "company_name",
                "evidence",
            ],
        )
        writer.writeheader()
        for row in consolidated:
            writer.writerow(
                {
                    "ticker": row.ticker,
                    "cik": row.cik,
                    "valid_from": row.valid_from.isoformat(),
                    "valid_to": (
                        row.valid_to.isoformat()
                        if row.valid_to is not None
                        else ""
                    ),
                    "company_name": row.company_name,
                    "evidence": row.evidence,
                }
            )

    args.review_output.parent.mkdir(parents=True, exist_ok=True)
    with args.review_output.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "ticker",
            "candidate_cik",
            "year",
            "reason",
            "sec_name",
            "source_file",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in review:
            writer.writerow({key: item.get(key, "") for key in fieldnames})

    print("HISTORICAL IDENTITY OVERRIDE CONSOLIDATION")
    print(f"Candidate files:             {len(candidate_files)}")
    print(f"Strong candidates promoted: {promoted}")
    print(f"Candidates held for review: {skipped}")
    print(f"Existing + candidate rows:  {len(rows)}")
    print(f"Consolidated intervals:     {len(consolidated)}")
    print(f"Conflicts/review rows:      {len(review)}")
    print(f"Proposed overrides:         {args.output}")
    print(f"Review report:              {args.review_output}")


def _load_existing(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                Row(
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
                    source="existing_overrides",
                )
            )
    return rows


def _year_from_filename(path: Path) -> int | None:
    match = re.search(r"(20\d{2})", path.stem)
    return int(match.group(1)) if match else None


def _consolidate(rows: list[Row]) -> tuple[list[Row], list[dict[str, str]]]:
    grouped: dict[tuple[str, int], list[Row]] = {}
    ticker_ciks: dict[str, set[int]] = {}

    for row in rows:
        grouped.setdefault((row.ticker, row.cik), []).append(row)
        ticker_ciks.setdefault(row.ticker, set()).add(row.cik)

    conflicts: list[dict[str, str]] = []
    for ticker, ciks in ticker_ciks.items():
        if len(ciks) <= 1:
            continue

        ticker_rows = sorted(
            [row for row in rows if row.ticker == ticker],
            key=lambda row: row.valid_from,
        )
        for left, right in zip(ticker_rows, ticker_rows[1:]):
            if left.cik == right.cik:
                continue
            if _overlap(left, right):
                conflicts.append(
                    {
                        "ticker": ticker,
                        "candidate_cik": f"{left.cik} / {right.cik}",
                        "year": "",
                        "reason": (
                            "overlapping intervals with different CIKs; "
                            "manual review required"
                        ),
                        "sec_name": (
                            f"{left.company_name} / {right.company_name}"
                        ),
                        "source_file": (
                            f"{left.source} / {right.source}"
                        ),
                    }
                )

    result: list[Row] = []

    for (_, _), group in grouped.items():
        ordered = sorted(group, key=lambda row: row.valid_from)
        current = ordered[0]

        for nxt in ordered[1:]:
            if _touch_or_overlap(current, nxt):
                current = Row(
                    ticker=current.ticker,
                    cik=current.cik,
                    valid_from=min(current.valid_from, nxt.valid_from),
                    valid_to=_max_end(current.valid_to, nxt.valid_to),
                    company_name=current.company_name or nxt.company_name,
                    evidence=_merge_evidence(current.evidence, nxt.evidence),
                    source=f"{current.source};{nxt.source}",
                )
            else:
                result.append(current)
                current = nxt

        result.append(current)

    result.sort(key=lambda row: (row.ticker, row.valid_from, row.cik))
    return result, conflicts


def _touch_or_overlap(left: Row, right: Row) -> bool:
    if left.valid_to is None:
        return True
    return right.valid_from <= left.valid_to


def _overlap(left: Row, right: Row) -> bool:
    left_end = left.valid_to
    right_end = right.valid_to

    if left_end is None and right_end is None:
        return True
    if left_end is None:
        return right_end is None or right_end > left.valid_from
    if right_end is None:
        return left_end > right.valid_from

    return (
        left.valid_from < right_end
        and right.valid_from < left_end
    )


def _max_end(left: date | None, right: date | None) -> date | None:
    if left is None or right is None:
        return None
    return max(left, right)


def _merge_evidence(left: str, right: str) -> str:
    pieces: list[str] = []
    for value in (left, right):
        if value and value not in pieces:
            pieces.append(value)
    return "; ".join(pieces)


def _float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


if __name__ == "__main__":
    main()
