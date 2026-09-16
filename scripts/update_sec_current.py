from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd

from finance.data.sec_recovery import failed_tickers, merge_targeted_recovery
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
        "--max-retries",
        type=int,
        default=3,
        help="Per-request retries for SEC HTTP 429/5xx responses.",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=1.0,
        help="Initial exponential backoff in seconds for transient SEC errors.",
    )
    parser.add_argument(
        "--recovery-attempts",
        type=int,
        default=1,
        help=(
            "Targeted passes for tickers still partial/error after the initial "
            "full-universe pass. Set 0 to disable."
        ),
    )
    parser.add_argument(
        "--recovery-request-delay",
        type=float,
        default=0.5,
        help="SEC request delay used during targeted recovery passes.",
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


def known_sec_state(path: Path) -> tuple[dict[int, set[str]], dict[int, pd.Timestamp]]:
    if not path.exists():
        return {}, {}

    frame = pd.read_csv(
        path,
        usecols=lambda column: column in {"cik", "adsh", "accepted_at"},
        dtype={"adsh": "string"},
        low_memory=False,
    )
    required = {"cik", "adsh"}
    if frame.empty or not required.issubset(frame.columns):
        return {}, {}

    frame["cik"] = pd.to_numeric(frame["cik"], errors="coerce")
    if "accepted_at" in frame.columns:
        frame["accepted_at"] = pd.to_datetime(frame["accepted_at"], errors="coerce")
    frame = frame.dropna(subset=["cik", "adsh"])

    accessions: dict[int, set[str]] = {}
    latest_accepted: dict[int, pd.Timestamp] = {}
    for cik, group in frame.groupby("cik"):
        key = int(cik)
        accessions[key] = set(group["adsh"].dropna().astype(str))
        if "accepted_at" in group.columns:
            eligible = group["accepted_at"].dropna()
            if not eligible.empty:
                latest_accepted[key] = eligible.max()

    return accessions, latest_accepted


def process_member(
    *,
    member,
    client: SecCurrentClient,
    args: argparse.Namespace,
    known: dict[int, set[str]],
    latest_accepted: dict[int, pd.Timestamp],
) -> list[dict]:
    cik = int(member.cik)
    ticker = member.ticker.upper()

    submissions_path = args.cache_dir / "submissions" / f"CIK{cik:010d}.json"
    try:
        submissions = client.submissions(cik)
        write_json_atomic(submissions_path, submissions)
    except Exception as exc:
        return [
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
        ]

    filings = [
        row
        for row in recent_filings_from_submissions(submissions)
        if row.filing_date <= args.as_of
    ]
    newest_known = latest_accepted.get(cik)
    new_filings = []
    for row in filings:
        if row.accession in known.get(cik, set()):
            continue

        if newest_known is not None:
            if pd.Timestamp(row.filing_date) <= newest_known.normalize():
                continue

        new_filings.append(row)

    if not new_filings:
        return [
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
        ]

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

    result_rows: list[dict] = []
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

        result_rows.append(
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
    return result_rows


def write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    if not args.user_agent.strip():
        raise SystemExit(
            "SEC user agent is required. Set SEC_USER_AGENT, for example: "
            "'finance-research your-email@example.com'."
        )
    if args.recovery_attempts < 0:
        raise SystemExit("--recovery-attempts must be >= 0")

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

    member_by_ticker = {member.ticker.upper(): member for member in members}

    print(f"Companies to inspect:     {len(members):,}", flush=True)
    known, latest_accepted = known_sec_state(args.winner_facts)
    print("Loaded existing SEC accession/timing index.", flush=True)

    client = SecCurrentClient(
        user_agent=args.user_agent,
        request_delay_seconds=args.request_delay,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff,
    )

    rows: list[dict] = []
    started = time.monotonic()

    for index, member in enumerate(members, start=1):
        cik = int(member.cik)
        ticker = member.ticker.upper()
        elapsed = (time.monotonic() - started) / 60
        print(
            f"[{index}/{len(members)}] {ticker} CIK={cik} "
            f"requests={client.request_count} retries={client.retry_count} "
            f"elapsed={elapsed:.1f}m",
            flush=True,
        )
        rows.extend(
            process_member(
                member=member,
                client=client,
                args=args,
                known=known,
                latest_accepted=latest_accepted,
            )
        )

    discovery = pd.DataFrame(rows)
    for recovery_number in range(1, args.recovery_attempts + 1):
        tickers = failed_tickers(discovery)
        if not tickers:
            break

        print()
        print(
            f"TARGETED RECOVERY {recovery_number}/{args.recovery_attempts}: "
            f"{len(tickers)} ticker(s): {', '.join(tickers)}",
            flush=True,
        )
        recovery_client = SecCurrentClient(
            user_agent=args.user_agent,
            request_delay_seconds=args.recovery_request_delay,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff,
        )
        recovery_rows: list[dict] = []
        for ticker in tickers:
            member = member_by_ticker.get(ticker)
            if member is None:
                continue
            print(f"    retrying {ticker}", flush=True)
            recovery_rows.extend(
                process_member(
                    member=member,
                    client=recovery_client,
                    args=args,
                    known=known,
                    latest_accepted=latest_accepted,
                )
            )
        client.request_count += recovery_client.request_count
        client.retry_count += recovery_client.retry_count
        discovery = merge_targeted_recovery(
            discovery,
            pd.DataFrame(recovery_rows),
        )

    rows = discovery.to_dict(orient="records")
    write_report(args.output, rows)

    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        statuses[status] = statuses.get(status, 0) + 1

    print()
    print("DISCOVERY COMPLETE", flush=True)
    print(f"HTTP requests:             {client.request_count:,}", flush=True)
    print(f"Transient retries:         {client.retry_count:,}", flush=True)
    print(f"Elapsed:                   {(time.monotonic() - started)/60:.1f}m", flush=True)
    for status, count in sorted(statuses.items()):
        print(f"{status:24s} {count:6,d}", flush=True)
    print(f"Report:                    {args.output}", flush=True)
    print("Canonical winner facts were NOT modified.", flush=True)


if __name__ == "__main__":
    main()
