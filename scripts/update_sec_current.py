from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd

from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.sec_current import (
    SecCurrentClient,
    parse_acceptance_datetime,
    recent_filings_from_submissions,
    write_json_atomic,
    write_text_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover and cache current SEC filing evidence for active S&P 500 "
            "members. Read-only: does not modify canonical winner facts."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--winner-facts",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", ""),
        help=(
            "SEC-compliant User-Agent identifying the application/contact. "
            "Defaults to SEC_USER_AGENT."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.2,
    )
    parser.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="Optional PIT ticker filter; repeat for multiple tickers.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of active companies to inspect.",
    )
    return parser.parse_args()


def active_members(intervals, as_of: date):
    rows = [
        row
        for row in intervals
        if row.start_date <= as_of
        and (row.end_date is None or as_of < row.end_date)
        and row.cik is not None
    ]
    return sorted(rows, key=lambda row: row.ticker)


def known_accessions_by_cik(path: Path) -> dict[int, set[str]]:
    if not path.exists():
        return {}

    frame = pd.read_csv(
        path,
        usecols=lambda column: column in {"cik", "adsh"},
        dtype={"adsh": "string"},
        low_memory=False,
    )
    if frame.empty or "cik" not in frame.columns or "adsh" not in frame.columns:
        return {}

    frame["cik"] = pd.to_numeric(frame["cik"], errors="coerce")
    frame = frame.dropna(subset=["cik", "adsh"])

    result: dict[int, set[str]] = {}
    for cik, group in frame.groupby("cik"):
        result[int(cik)] = set(group["adsh"].dropna().astype(str))
    return result


def main() -> None:
    args = parse_args()
    if not args.user_agent.strip():
        raise SystemExit(
            "SEC user agent is required. Set SEC_USER_AGENT, for example: "
            "'finance-research your-email@example.com'."
        )

    print("SEC CURRENT FILING DISCOVERY", flush=True)
    print(f"As of:                    {args.as_of}", flush=True)
    print(f"PITIndex:                 {args.pitindex_data}", flush=True)
    print(f"Winner cache:             {args.winner_facts}", flush=True)
    print(f"Evidence cache:           {args.cache_dir}", flush=True)

    intervals = load_pitindex_sp500(args.pitindex_data)
    members = active_members(intervals, args.as_of)

    requested_tickers = {ticker.strip().upper() for ticker in args.ticker if ticker.strip()}
    if requested_tickers:
        members = [
            row for row in members
            if row.ticker.upper() in requested_tickers
        ]

    if args.limit is not None:
        members = members[: args.limit]

    print(f"Companies to inspect:     {len(members):,}", flush=True)
    known = known_accessions_by_cik(args.winner_facts)
    print("Loaded existing SEC accession index.", flush=True)

    client = SecCurrentClient(
        user_agent=args.user_agent,
        request_delay_seconds=args.request_delay,
    )

    rows: list[dict] = []
    started = time.monotonic()

    for index, member in enumerate(members, start=1):
        cik = int(member.cik)
        ticker = member.ticker.upper()
        elapsed = (time.monotonic() - started) / 60
        print(
            f"[{index}/{len(members)}] {ticker} CIK={cik} "
            f"requests={client.request_count} elapsed={elapsed:.1f}m",
            flush=True,
        )

        submissions_path = args.cache_dir / "submissions" / f"CIK{cik:010d}.json"
        try:
            submissions = client.submissions(cik)
            write_json_atomic(submissions_path, submissions)
        except Exception as exc:
            rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": "submissions_error",
                    "accession": "",
                    "form": "",
                    "filing_date": "",
                    "report_date": "",
                    "accepted_at": "",
                    "companyfacts_cached": False,
                    "error": str(exc),
                }
            )
            continue

        filings = [
            row
            for row in recent_filings_from_submissions(submissions)
            if row.filing_date <= args.as_of
        ]
        new_filings = [
            row for row in filings
            if row.accession not in known.get(cik, set())
        ]

        if not new_filings:
            rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": "no_new_filing",
                    "accession": "",
                    "form": "",
                    "filing_date": "",
                    "report_date": "",
                    "accepted_at": "",
                    "companyfacts_cached": False,
                    "error": "",
                }
            )
            continue

        print(
            f"    new supported filings: {len(new_filings)}",
            flush=True,
        )

        companyfacts_path = args.cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        companyfacts_cached = False
        companyfacts_error = ""
        try:
            companyfacts = client.companyfacts(cik)
            write_json_atomic(companyfacts_path, companyfacts)
            companyfacts_cached = True
        except Exception as exc:
            companyfacts_error = str(exc)

        for filing in sorted(new_filings, key=lambda row: (row.filing_date, row.accession)):
            header_path = (
                args.cache_dir
                / "headers"
                / str(cik)
                / f"{filing.accession}.hdr.sgml"
            )
            accepted_at = ""
            header_error = ""
            try:
                header = client.filing_header(cik, filing.accession)
                write_text_atomic(header_path, header)
                accepted_at = parse_acceptance_datetime(header).isoformat()
            except Exception as exc:
                header_error = str(exc)

            errors = " | ".join(
                error for error in (companyfacts_error, header_error) if error
            )
            status = "new_filing_cached" if not errors else "new_filing_partial"

            rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": status,
                    "accession": filing.accession,
                    "form": filing.form,
                    "filing_date": filing.filing_date.isoformat(),
                    "report_date": (
                        filing.report_date.isoformat()
                        if filing.report_date
                        else ""
                    ),
                    "accepted_at": accepted_at,
                    "companyfacts_cached": companyfacts_cached,
                    "error": errors,
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "cik",
        "status",
        "accession",
        "form",
        "filing_date",
        "report_date",
        "accepted_at",
        "companyfacts_cached",
        "error",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1

    print()
    print("DISCOVERY COMPLETE", flush=True)
    print(f"HTTP requests:             {client.request_count:,}", flush=True)
    print(f"Elapsed:                   {(time.monotonic() - started)/60:.1f}m", flush=True)
    for status, count in sorted(statuses.items()):
        print(f"{status:24s} {count:6,d}", flush=True)
    print(f"Report:                    {args.output}", flush=True)
    print("Canonical winner facts were NOT modified.", flush=True)


if __name__ == "__main__":
    main()
