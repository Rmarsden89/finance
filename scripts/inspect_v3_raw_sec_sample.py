from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
import os
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_current import (
    SUPPORTED_FORMS,
    SecCurrentClient,
    parse_acceptance_datetime,
    recent_filings_from_submissions,
)
from finance.research.sec_raw_filing import (
    filing_document_url,
    filing_header_url,
    parse_inline_xbrl_evidence,
    sha256_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect research-only raw SEC Inline XBRL evidence for the frozen "
            "Issue #26 overlap-validation cohort."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--sample-path", type=Path, default=None)
    parser.add_argument("--output-subdir", default="raw_sec")
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", ""),
        help="SEC-compliant User-Agent; defaults to SEC_USER_AGENT.",
    )
    return parser.parse_args()


def _latest_supported_filing(
    client: SecCurrentClient,
    *,
    cik: int,
    as_of: date,
):
    payload = client.submissions(cik)
    records = recent_filings_from_submissions(
        payload,
        supported_forms=set(SUPPORTED_FORMS),
    )
    eligible = [
        row
        for row in records
        if row.primary_document
        and row.filing_date < as_of
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda row: (
            row.filing_date,
            row.report_date or date.min,
            row.accession,
        ),
        reverse=True,
    )
    return eligible[0]


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    if not args.user_agent.strip():
        raise SystemExit(
            "SEC_USER_AGENT is required for raw SEC evidence collection."
        )

    sample_path = (
        args.sample_path.resolve()
        if args.sample_path is not None
        else (
            root
            / "reports"
            / "v3"
            / "data_sources"
            / args.as_of.isoformat()
            / "gap_inventory"
            / "overlap_validation_sample.csv"
        )
    )
    if not sample_path.exists():
        raise SystemExit(f"Missing validation sample: {sample_path}")

    sample = pd.read_csv(sample_path, low_memory=False)
    required = {"ticker", "cik", "sample_cohort"}
    missing = sorted(required - set(sample.columns))
    if missing:
        raise SystemExit(
            "Validation sample missing required columns: " + ", ".join(missing)
        )

    client = SecCurrentClient(user_agent=args.user_agent)
    output_dir = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / args.output_subdir
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    evidence_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []

    for row in sample.itertuples(index=False):
        ticker = str(row.ticker).upper()
        cik = int(float(row.cik))
        accession_text = str(getattr(row, "discovery_accessions", "") or "").strip()
        accession = ""
        primary_document = ""
        selection_method = ""

        if accession_text and accession_text.lower() != "nan":
            accessions = [
                value.strip()
                for value in accession_text.split("|")
                if value.strip()
            ]
            if accessions:
                accession = accessions[0]
                selection_method = "sample_discovery_accession"

        if accession:
            submissions = client.submissions(cik)
            records = {
                item.accession: item
                for item in recent_filings_from_submissions(submissions)
            }
            record = records.get(accession)
            if record and record.primary_document:
                primary_document = record.primary_document
            else:
                fallback = _latest_supported_filing(
                    client,
                    cik=cik,
                    as_of=args.as_of,
                )
                if fallback is None:
                    raise SystemExit(
                        f"No supported SEC filing found for {ticker} ({cik})"
                    )
                accession = fallback.accession
                primary_document = fallback.primary_document or ""
                selection_method = "latest_supported_filing_fallback"
        else:
            record = _latest_supported_filing(
                client,
                cik=cik,
                as_of=args.as_of,
            )
            if record is None or not record.primary_document:
                raise SystemExit(
                    f"No supported SEC filing found for {ticker} ({cik})"
                )
            accession = record.accession
            primary_document = record.primary_document
            selection_method = "latest_supported_filing"

        header_url = filing_header_url(cik=cik, accession=accession)
        document_url = filing_document_url(
            cik=cik,
            accession=accession,
            primary_document=primary_document,
        )
        header = client.get_text(header_url)
        html = client.get_text(document_url)
        accepted_at = parse_acceptance_datetime(header).isoformat()

        ticker_dir = output_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        header_path = ticker_dir / f"{accession}.hdr.sgml"
        html_path = ticker_dir / primary_document
        header_path.write_text(header, encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")

        facts, contexts = parse_inline_xbrl_evidence(html)
        for fact in facts:
            context = contexts.get(fact.context_ref)
            evidence_rows.append(
                {
                    "ticker": ticker,
                    "sample_cohort": getattr(row, "sample_cohort", ""),
                    "sample_cik": cik,
                    "accession": accession,
                    "selection_method": selection_method,
                    "accepted_at": accepted_at,
                    "primary_document": primary_document,
                    "fact_name": fact.name,
                    "context_ref": fact.context_ref,
                    "context_instant": context.instant if context else "",
                    "context_start_date": context.start_date if context else "",
                    "context_end_date": context.end_date if context else "",
                    "context_dimensions": context.dimensions if context else "",
                    "unit_ref": fact.unit_ref,
                    "decimals": fact.decimals,
                    "scale": fact.scale,
                    "sign": fact.sign,
                    "value_text": fact.value_text,
                    "source_url": document_url,
                }
            )

        manifest_rows.append(
            {
                "ticker": ticker,
                "sample_cohort": getattr(row, "sample_cohort", ""),
                "sample_cik": cik,
                "accession": accession,
                "selection_method": selection_method,
                "accepted_at": accepted_at,
                "primary_document": primary_document,
                "header_url": header_url,
                "document_url": document_url,
                "header_sha256": sha256_text(header),
                "document_sha256": sha256_text(html),
                "candidate_fact_count": len(facts),
                "context_count": len(contexts),
                "header_path": str(header_path),
                "document_path": str(html_path),
            }
        )

    evidence = pd.DataFrame(evidence_rows)
    manifest = pd.DataFrame(manifest_rows)
    evidence_path = output_dir / "inline_xbrl_candidate_facts.csv"
    manifest_path = output_dir / "filing_manifest.csv"
    summary_path = output_dir / "summary.json"

    evidence.to_csv(evidence_path, index=False)
    manifest.to_csv(manifest_path, index=False)

    summary = {
        "as_of": args.as_of.isoformat(),
        "sample_rows": int(len(sample)),
        "filings_collected": int(len(manifest)),
        "candidate_fact_rows": int(len(evidence)),
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "research_only": True,
        "model_inputs_modified": False,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 RAW SEC EVIDENCE COLLECTION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Sample rows:                 {len(sample)}")
    print(f"Filings collected:           {len(manifest)}")
    print(f"Candidate fact rows:         {len(evidence)}")
    print(f"SEC requests:                {client.request_count}")
    print(f"SEC retries:                 {client.retry_count}")
    print(f"Manifest:                    {manifest_path}")
    print(f"Candidate facts:             {evidence_path}")
    print(f"Summary:                     {summary_path}")
    print("NO FACTS WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
