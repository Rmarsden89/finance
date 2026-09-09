from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from finance.data.sec_current_facts import (
    extract_companyfacts_candidates,
    load_companyfacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build review-only canonical candidate facts from cached current "
            "SEC companyfacts evidence. Does not modify winner facts."
        )
    )
    parser.add_argument(
        "--discovery",
        type=Path,
        required=True,
        help="CSV emitted by scripts/update_sec_current.py.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_current_candidate_facts.csv"),
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("reports/sec_current_candidate_audit.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    discovery = pd.read_csv(args.discovery, low_memory=False)
    usable = discovery.loc[
        discovery["status"].astype(str).eq("new_filing_cached")
    ].copy()

    print("SEC CURRENT CANDIDATE FACT BUILD", flush=True)
    print(f"Discovery rows:            {len(discovery):,}", flush=True)
    print(f"Usable filings:            {len(usable):,}", flush=True)
    print(f"Evidence cache:            {args.cache_dir}", flush=True)

    if usable.empty:
        raise SystemExit("No new_filing_cached rows available in discovery CSV.")

    frames: list[pd.DataFrame] = []
    audit_rows: list[dict] = []
    started = time.monotonic()

    for index, row in enumerate(usable.itertuples(index=False), start=1):
        cik = int(row.cik)
        ticker = str(row.ticker)
        accession = str(row.accession)
        companyfacts_path = (
            args.cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        )

        elapsed = (time.monotonic() - started) / 60
        print(
            f"[{index}/{len(usable)}] {ticker} {accession} "
            f"elapsed={elapsed:.1f}m",
            flush=True,
        )

        if not companyfacts_path.exists():
            audit_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "accession": accession,
                    "status": "missing_companyfacts_cache",
                    "concepts_seen": 0,
                    "source_rows_seen": 0,
                    "rows_matching_accession": 0,
                    "rows_current_period": 0,
                    "rows_period_eligible": 0,
                    "rows_output": 0,
                    "error": str(companyfacts_path),
                }
            )
            continue

        try:
            payload = load_companyfacts(companyfacts_path)
            company_name = str(
                payload.get("entityName")
                or getattr(row, "company_name", "")
                or ticker
            )
            frame, audit = extract_companyfacts_candidates(
                payload,
                accession=accession,
                cik=cik,
                company_name=company_name,
                form=str(row.form),
                report_date=pd.Timestamp(row.report_date).date(),
                filed_date=pd.Timestamp(row.filing_date).date(),
                accepted_at=str(row.accepted_at),
            )
            if not frame.empty:
                frame.insert(0, "ticker", ticker)
                frames.append(frame)

            audit_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "accession": accession,
                    "status": "ok" if not frame.empty else "no_candidates",
                    "concepts_seen": audit.concepts_seen,
                    "source_rows_seen": audit.source_rows_seen,
                    "rows_matching_accession": audit.rows_matching_accession,
                    "rows_current_period": audit.rows_current_period,
                    "rows_period_eligible": audit.rows_period_eligible,
                    "rows_output": audit.rows_output,
                    "error": "",
                }
            )
            print(
                f"    candidates={audit.rows_output} "
                f"accession_rows={audit.rows_matching_accession} "
                f"current_period={audit.rows_current_period}",
                flush=True,
            )
        except Exception as exc:
            audit_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "accession": accession,
                    "status": "error",
                    "concepts_seen": 0,
                    "source_rows_seen": 0,
                    "rows_matching_accession": 0,
                    "rows_current_period": 0,
                    "rows_period_eligible": 0,
                    "rows_output": 0,
                    "error": str(exc),
                }
            )

    candidates = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame()
    )
    audits = pd.DataFrame(audit_rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(args.output, index=False)
    audits.to_csv(args.audit_output, index=False)

    print()
    print("CANDIDATE BUILD COMPLETE", flush=True)
    print(f"Candidate rows:            {len(candidates):,}", flush=True)
    print(f"Filings with candidates:   {(audits['status'] == 'ok').sum():,}", flush=True)
    print(f"Elapsed:                   {(time.monotonic() - started)/60:.1f}m", flush=True)
    print(f"Candidates:                {args.output}", flush=True)
    print(f"Audit:                     {args.audit_output}", flush=True)
    print("Canonical winner facts were NOT modified.", flush=True)


if __name__ == "__main__":
    main()
