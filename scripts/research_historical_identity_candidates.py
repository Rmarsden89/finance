from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from finance.data.historical_identity_candidates import generate_identity_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate SEC-backed candidate CIKs for unresolved historical identities."
    )
    parser.add_argument("--identity-audit", type=Path, required=True)
    parser.add_argument("--sec-financial-statements-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--fuzzy-threshold", type=float, default=0.92)
    parser.add_argument("--fuzzy-margin", type=float, default=0.04)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/historical_identity_candidates.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = datetime.fromisoformat(args.as_of)

    with args.identity_audit.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    unresolved = [
        row
        for row in rows
        if row.get("resolution_method") == "unresolved"
        or row.get("status") == "unresolved"
    ]

    zip_paths = sorted(args.sec_financial_statements_dir.glob("*.zip"))
    candidates = generate_identity_candidates(
        unresolved,
        sec_zip_paths=zip_paths,
        as_of=as_of,
        fuzzy_threshold=args.fuzzy_threshold,
        fuzzy_margin=args.fuzzy_margin,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ticker",
        "company_name",
        "candidate_cik",
        "sec_name",
        "method",
        "evidence_count",
        "score",
        "first_seen",
        "last_seen",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "ticker": candidate.ticker,
                    "company_name": candidate.company_name or "",
                    "candidate_cik": candidate.candidate_cik,
                    "sec_name": candidate.sec_name,
                    "method": candidate.method,
                    "evidence_count": candidate.evidence_count,
                    "score": f"{candidate.score:.4f}",
                    "first_seen": candidate.first_seen,
                    "last_seen": candidate.last_seen,
                }
            )

    methods: dict[str, int] = {}
    for candidate in candidates:
        methods[candidate.method] = methods.get(candidate.method, 0) + 1

    print("HISTORICAL IDENTITY CANDIDATE RESEARCH")
    print(f"As of:                  {as_of}")
    print(f"Unresolved input:       {len(unresolved)}")
    print(f"Candidates generated:   {len(candidates)}")
    print(f"No candidate yet:       {len(unresolved) - len(candidates)}")
    print(f"Report:                  {args.output}")

    if methods:
        print()
        print("CANDIDATES BY METHOD")
        for method, count in sorted(methods.items()):
            print(f"{method:30s} {count:6d}")

    if candidates:
        print()
        print("CANDIDATES")
        for row in candidates:
            print(
                f"{row.ticker:6s} -> {row.candidate_cik:10d} "
                f"{row.method:26s} "
                f"score={row.score:.3f} "
                f"{row.sec_name}"
            )


if __name__ == "__main__":
    main()
