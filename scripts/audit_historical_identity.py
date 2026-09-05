from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from finance.data.historical_identity import resolve_memberships_as_of
from finance.data.membership import MembershipStore
from finance.data.sec_entity_history import load_sec_entity_evidence
from finance.data.universe_identity import build_enriched_sp500_intervals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit PIT S&P CIK mappings against historical SEC filer evidence."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--sec-tickers", type=Path)
    parser.add_argument("--sec-historical-names", type=Path)
    parser.add_argument("--datamule-dir", type=Path)
    parser.add_argument("--ticker-renames", type=Path)
    parser.add_argument("--sec-financial-statements-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/historical_identity_audit.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = datetime.fromisoformat(args.as_of)

    intervals = build_enriched_sp500_intervals(
        args.pitindex_data,
        sec_tickers=args.sec_tickers,
        ticker_renames=args.ticker_renames,
        datamule_dir=args.datamule_dir,
        sec_historical_names=args.sec_historical_names,
        start_year=2015,
    )
    members = MembershipStore(intervals).members_as_of(as_of.date())

    zip_paths = sorted(args.sec_financial_statements_dir.glob("*.zip"))
    evidence = load_sec_entity_evidence(zip_paths, as_of=as_of)
    resolutions = resolve_memberships_as_of(
        members,
        evidence_by_cik=evidence,
    )

    rows = []
    for member, resolution in zip(members, resolutions):
        resolved_evidence = (
            evidence.get(resolution.resolved_cik)
            if resolution.resolved_cik is not None
            else None
        )
        rows.append(
            {
                "ticker": member.ticker,
                "company_name": member.company_name or "",
                "original_cik": "" if member.cik is None else member.cik,
                "resolved_cik": (
                    "" if resolution.resolved_cik is None else resolution.resolved_cik
                ),
                "resolution_method": resolution.method,
                "sec_first_seen": (
                    "" if resolved_evidence is None else resolved_evidence.first_accepted_at
                ),
                "sec_last_seen_as_of": (
                    "" if resolved_evidence is None else resolved_evidence.last_accepted_at
                ),
                "sec_names": (
                    "" if resolved_evidence is None else "|".join(resolved_evidence.names)
                ),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    verified = sum(row["resolution_method"] == "existing_cik_verified" for row in rows)
    repaired = sum(row["resolution_method"] == "sec_name_as_of" for row in rows)
    unresolved = sum(row["resolution_method"] == "unresolved" for row in rows)

    print("HISTORICAL IDENTITY AUDIT")
    print(f"As of:                       {as_of}")
    print(f"S&P members:                 {len(rows)}")
    print(f"Existing CIK verified:       {verified}")
    print(f"Resolved by SEC name as-of:  {repaired}")
    print(f"Still unresolved:            {unresolved}")
    print(f"Historical identity coverage:{(verified + repaired) / len(rows):10.1%}")
    print(f"Report:                      {args.output}")

    if repaired:
        print()
        print("AS-OF CIK REPAIRS")
        for row in rows:
            if row["resolution_method"] == "sec_name_as_of":
                print(
                    f"{row['ticker']:6s} "
                    f"{row['original_cik'] or '-':>10} -> "
                    f"{row['resolved_cik']:>10}  "
                    f"{row['company_name']}"
                )

    if unresolved:
        print()
        print("STILL UNRESOLVED")
        for row in rows:
            if row["resolution_method"] == "unresolved":
                print(
                    f"{row['ticker']:6s} "
                    f"{row['company_name']}"
                )


if __name__ == "__main__":
    main()
