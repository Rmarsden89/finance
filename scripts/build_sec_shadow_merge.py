from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from finance.data.sec_shadow_merge import merge_current_sec_shadow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a shadow SEC winner history by conservatively extending the "
            "quarterly archive with review-only current companyfacts candidates."
        )
    )
    parser.add_argument(
        "--historical",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument("--current-candidates", type=Path, required=True)
    parser.add_argument(
        "--as-of",
        type=pd.Timestamp,
        help="Optional acceptance-time cutoff for current facts.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cache/sec/shadow/sec_winner_facts_shadow.csv"),
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("reports/sec_shadow_merge_audit.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_shadow_merge_summary.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("SEC SHADOW WINNER MERGE", flush=True)
    print(f"Historical:                {args.historical}", flush=True)
    print(f"Current candidates:        {args.current_candidates}", flush=True)
    print(f"As of:                     {args.as_of or 'no cutoff'}", flush=True)

    print("Loading historical winner cache...", flush=True)
    historical = pd.read_csv(args.historical, low_memory=False)
    print(f"Historical rows loaded:    {len(historical):,}", flush=True)

    print("Loading current candidate facts...", flush=True)
    current = pd.read_csv(args.current_candidates, low_memory=False)
    print(f"Current rows loaded:       {len(current):,}", flush=True)

    print("Applying shadow merge guardrails...", flush=True)
    merged, audit, summary = merge_current_sec_shadow(
        historical,
        current,
        as_of=args.as_of,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)

    print("Writing shadow cache...", flush=True)
    merged.to_csv(args.output, index=False)
    audit.to_csv(args.audit_output, index=False)
    pd.DataFrame([asdict(summary)]).to_csv(args.summary_output, index=False)

    print()
    print("SHADOW MERGE COMPLETE", flush=True)
    print(f"Historical rows:           {summary.historical_rows:,}", flush=True)
    print(f"Current rows input:        {summary.current_rows_input:,}", flush=True)
    print(f"Current rows valid:        {summary.current_rows_valid:,}", flush=True)
    print(f"Current winners added:     {summary.current_rows_added:,}", flush=True)
    print(
        f"Rejected invalid timing:   {summary.rejected_invalid_acceptance:,}",
        flush=True,
    )
    print(
        f"Rejected unsupported:      {summary.rejected_unsupported_concept:,}",
        flush=True,
    )
    print(
        f"Rejected existing archive: {summary.rejected_existing_fact_group:,}",
        flush=True,
    )
    print(
        f"Rejected ambiguous values: {summary.rejected_ambiguous_value_group:,}",
        flush=True,
    )
    print(
        f"Rejected unresolved:       {summary.rejected_unresolved_winner_group:,}",
        flush=True,
    )
    print(f"Merged rows:               {summary.merged_rows:,}", flush=True)
    print(f"Shadow cache:              {args.output}", flush=True)
    print(f"Audit:                     {args.audit_output}", flush=True)
    print(f"Summary:                   {args.summary_output}", flush=True)
    print("Production historical winner cache was NOT modified.", flush=True)


if __name__ == "__main__":
    main()
