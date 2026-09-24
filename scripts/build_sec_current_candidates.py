from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from finance.data.sec_current_facts import (
    extract_companyfacts_candidates,
    load_companyfacts,
)


SHARE_FALLBACK_POLICIES = {
    "v1_exact_only": {
        "allow_dei_share_fallback": False,
        "allow_bounded_dei_cover_date": False,
    },
    "v2_dei_cover_date": {
        "allow_dei_share_fallback": True,
        "allow_bounded_dei_cover_date": True,
    },
}
V2_ARTIFACT_ROOT = Path("reports") / "v2" / "long_growth_v2_research"


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
    parser.add_argument(
        "--share-fallback-policy",
        choices=sorted(SHARE_FALLBACK_POLICIES),
        default="v1_exact_only",
        help=(
            "V1 exact-only is the frozen default. The V2 policy enables the "
            "research-only DEI exact and bounded cover-date fallbacks and "
            "requires outputs under reports/v2/long_growth_v2_research."
        ),
    )
    return parser.parse_args()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_output_isolation(
    *,
    policy: str,
    output: Path,
    audit_output: Path,
    repo_root: Path,
) -> None:
    if policy != "v2_dei_cover_date":
        return
    v2_root = repo_root / V2_ARTIFACT_ROOT
    outside = [
        str(path)
        for path in (output, audit_output)
        if not _is_within(path, v2_root)
    ]
    if outside:
        raise ValueError(
            "V2 candidate outputs must stay under "
            f"{v2_root}: {', '.join(outside)}"
        )


def main() -> None:
    args = parse_args()
    validate_output_isolation(
        policy=args.share_fallback_policy,
        output=args.output,
        audit_output=args.audit_output,
        repo_root=Path.cwd(),
    )
    fallback_options = SHARE_FALLBACK_POLICIES[args.share_fallback_policy]
    discovery = pd.read_csv(args.discovery, low_memory=False)
    usable = discovery.loc[
        discovery["status"].astype(str).eq("new_filing_cached")
    ].copy()

    print("SEC CURRENT CANDIDATE FACT BUILD", flush=True)
    print(f"Discovery rows:            {len(discovery):,}", flush=True)
    print(f"Usable filings:            {len(usable):,}", flush=True)
    print(f"Evidence cache:            {args.cache_dir}", flush=True)
    print(f"Share fallback policy:     {args.share_fallback_policy}", flush=True)

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
                    "share_fallback_policy": args.share_fallback_policy,
                    "concepts_seen": 0,
                    "source_rows_seen": 0,
                    "rows_matching_accession": 0,
                    "rows_current_period": 0,
                    "rows_period_eligible": 0,
                    "bounded_share_candidates_seen": 0,
                    "bounded_share_candidates_selected": 0,
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
                **fallback_options,
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
                    "share_fallback_policy": args.share_fallback_policy,
                    "concepts_seen": audit.concepts_seen,
                    "source_rows_seen": audit.source_rows_seen,
                    "rows_matching_accession": audit.rows_matching_accession,
                    "rows_current_period": audit.rows_current_period,
                    "rows_period_eligible": audit.rows_period_eligible,
                    "bounded_share_candidates_seen": (
                        audit.bounded_share_candidates_seen
                    ),
                    "bounded_share_candidates_selected": (
                        audit.bounded_share_candidates_selected
                    ),
                    "rows_output": audit.rows_output,
                    "error": "",
                }
            )
            print(
                f"    candidates={audit.rows_output} "
                f"accession_rows={audit.rows_matching_accession} "
                f"current_period={audit.rows_current_period}",
                f"bounded_shares={audit.bounded_share_candidates_selected}/"
                f"{audit.bounded_share_candidates_seen}",
                flush=True,
            )
        except Exception as exc:
            audit_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "accession": accession,
                    "status": "error",
                    "share_fallback_policy": args.share_fallback_policy,
                    "concepts_seen": 0,
                    "source_rows_seen": 0,
                    "rows_matching_accession": 0,
                    "rows_current_period": 0,
                    "rows_period_eligible": 0,
                    "bounded_share_candidates_seen": 0,
                    "bounded_share_candidates_selected": 0,
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
