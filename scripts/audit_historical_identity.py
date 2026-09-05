from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

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

    rows = []
    for member in members:
        cik = member.cik
        sec = evidence.get(cik) if cik is not None else None
        status = (
            "unresolved"
            if cik is None
            else "verified_as_of"
            if sec is not None
            else "mapped_cik_not_seen_as_of"
        )

        rows.append(
            {
                "ticker": member.ticker,
                "company_name": member.company_name or "",
                "mapped_cik": "" if cik is None else cik,
                "status": status,
                "sec_first_seen": "" if sec is None else sec.first_accepted_at,
                "sec_last_seen_as_of": "" if sec is None else sec.last_accepted_at,
                "sec_names": "" if sec is None else "|".join(sec.names),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    unresolved = sum(row["status"] == "unresolved" for row in rows)
    invalid = sum(row["status"] == "mapped_cik_not_seen_as_of" for row in rows)
    verified = sum(row["status"] == "verified_as_of" for row in rows)

    print("HISTORICAL IDENTITY AUDIT")
    print(f"As of:                       {as_of}")
    print(f"S&P members:                 {len(rows)}")
    print(f"CIK verified as of date:     {verified}")
    print(f"Mapped CIK not seen as of:   {invalid}")
    print(f"CIK unresolved:              {unresolved}")
    print(f"Report:                      {args.output}")

    if invalid:
        print()
        print("MAPPED CIKS NOT SEEN AS OF DATE")
        for row in rows:
            if row["status"] == "mapped_cik_not_seen_as_of":
                print(
                    f"{row['ticker']:6s} "
                    f"CIK={row['mapped_cik']} "
                    f"{row['company_name']}"
                )


if __name__ == "__main__":
    main()
