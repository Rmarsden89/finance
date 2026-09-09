from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from finance.data.sec_shadow_impact import compare_sec_latest_state
from finance.data.sources.pitindex import load_pitindex_sp500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare quarterly-only SEC latest facts with the current SEC shadow "
            "latest state for the active PITIndex universe."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--historical",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument(
        "--shadow",
        type=Path,
        default=Path("data/cache/sec/shadow/sec_winner_facts_shadow.csv"),
    )
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument("--merge-audit", type=Path)
    parser.add_argument(
        "--detail-output",
        type=Path,
        default=Path("reports/sec_shadow_impact_detail.csv"),
    )
    parser.add_argument(
        "--ticker-output",
        type=Path,
        default=Path("reports/sec_shadow_impact_by_ticker.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_shadow_impact_summary.csv"),
    )
    parser.add_argument(
        "--ambiguity-output",
        type=Path,
        default=Path("reports/sec_shadow_impact_ambiguities.csv"),
    )
    return parser.parse_args()


def active_universe(path: Path, as_of: pd.Timestamp) -> pd.DataFrame:
    intervals = load_pitindex_sp500(path)
    day = as_of.date()
    rows = [
        {
            "ticker": row.ticker,
            "cik": row.cik,
            "company_name": row.company_name,
        }
        for row in intervals
        if row.start_date <= day
        and (row.end_date is None or day < row.end_date)
    ]
    return pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)


def main() -> None:
    args = parse_args()

    print("SEC SHADOW FUNDAMENTAL IMPACT", flush=True)
    print(f"As of:                     {args.as_of}", flush=True)
    print(f"Historical:                {args.historical}", flush=True)
    print(f"Shadow:                    {args.shadow}", flush=True)

    print("Loading active PITIndex universe...", flush=True)
    universe = active_universe(args.pitindex_data, args.as_of)
    print(f"Active members:            {len(universe):,}", flush=True)

    print("Loading quarterly-only winner facts...", flush=True)
    historical = pd.read_csv(args.historical, low_memory=False)
    print(f"Historical rows:           {len(historical):,}", flush=True)

    print("Loading shadow winner facts...", flush=True)
    shadow = pd.read_csv(args.shadow, low_memory=False)
    print(f"Shadow rows:               {len(shadow):,}", flush=True)

    audit = None
    if args.merge_audit is not None and args.merge_audit.exists():
        print("Loading shadow merge audit...", flush=True)
        audit = pd.read_csv(args.merge_audit, low_memory=False)

    print("Comparing latest PIT SEC state...", flush=True)
    detail, ticker_summary, summary, ambiguity = compare_sec_latest_state(
        historical,
        shadow,
        as_of=args.as_of,
        universe=universe,
        merge_audit=audit,
    )

    for path in (
        args.detail_output,
        args.ticker_output,
        args.summary_output,
        args.ambiguity_output,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    detail.to_csv(args.detail_output, index=False)
    ticker_summary.to_csv(args.ticker_output, index=False)
    pd.DataFrame([asdict(summary)]).to_csv(args.summary_output, index=False)
    ambiguity.to_csv(args.ambiguity_output, index=False)

    print()
    print("SEC SHADOW IMPACT COMPLETE", flush=True)
    print(f"Universe members:          {summary.universe_members:,}", flush=True)
    print(f"Members with CIK:          {summary.universe_members_with_cik:,}", flush=True)
    print(f"Changed latest facts:      {summary.changed_fact_groups:,}", flush=True)
    print(f"Fresher-period facts:      {summary.fresher_period_groups:,}", flush=True)
    print(f"Same-period newer filing:  {summary.same_period_newer_filing_groups:,}", flush=True)
    print(f"Newly available facts:     {summary.newly_available_groups:,}", flush=True)
    print(f"Value-changed facts:       {summary.value_changed_groups:,}", flush=True)
    print(f"Provenance-only changes:   {summary.provenance_only_groups:,}", flush=True)
    print(f"Tickers changed:           {summary.tickers_with_any_change:,}", flush=True)
    print(f"Tickers fresher:           {summary.tickers_with_fresher_period:,}", flush=True)
    print(f"Ambiguous rejected groups: {summary.ambiguous_rejection_groups:,}", flush=True)
    print(f"Detail:                    {args.detail_output}", flush=True)
    print(f"By ticker:                 {args.ticker_output}", flush=True)
    print(f"Summary:                   {args.summary_output}", flush=True)
    print(f"Ambiguities:               {args.ambiguity_output}", flush=True)


if __name__ == "__main__":
    main()
