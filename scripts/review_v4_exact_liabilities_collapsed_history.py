from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Review the deterministic collapsed historical identity evidence "
            "for the exact-match V4 liabilities cohort."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    cohort_path = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_current_noncurrent_diagnostic"
        / "clean_historical_replay_cohort.csv"
    )
    collapsed_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
        / "current_candidate_identity_collapsed.csv"
    )
    collapsed_by_ticker_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
        / "current_candidate_identity_collapsed_by_ticker.csv"
    )

    if not cohort_path.exists():
        raise SystemExit(f"Missing V4 clean cohort: {cohort_path}")
    if not collapsed_path.exists() or not collapsed_by_ticker_path.exists():
        raise SystemExit(
            "Missing collapsed historical identity artifacts. Run "
            "scripts/collapse_v3_current_liabilities_identity_corroboration.py "
            f"--as-of {as_of} first."
        )

    cohort = pd.read_csv(cohort_path, low_memory=False)
    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    cohort = cohort.loc[
        cohort["validation_band"].astype(str).eq("exact_match")
    ].copy()
    tickers = set(cohort["ticker"])
    if not tickers:
        raise SystemExit("No exact-match V4 liabilities cohort found")

    detail = pd.read_csv(collapsed_path, low_memory=False)
    detail["ticker"] = detail["ticker"].astype(str).str.upper().str.strip()
    detail = detail.loc[detail["ticker"].isin(tickers)].copy()

    by_ticker = pd.read_csv(collapsed_by_ticker_path, low_memory=False)
    by_ticker["ticker"] = by_ticker["ticker"].astype(str).str.upper().str.strip()
    by_ticker = by_ticker.loc[by_ticker["ticker"].isin(tickers)].copy()

    if detail.empty or by_ticker.empty:
        raise SystemExit(
            "Collapsed historical evidence contains no rows for "
            + ",".join(sorted(tickers))
        )

    material_rows = int(
        detail.get(
            "historically_material",
            pd.Series(False, index=detail.index),
        ).fillna(False).astype(bool).sum()
    )
    ambiguous_rows = int(
        detail.get(
            "historically_ambiguous",
            pd.Series(False, index=detail.index),
        ).fillna(False).astype(bool).sum()
    )
    clean_rows = int(
        detail.get(
            "historically_clean",
            pd.Series(False, index=detail.index),
        ).fillna(False).astype(bool).sum()
    )

    classifications = (
        by_ticker.set_index("ticker")["candidate_classification"]
        .astype(str)
        .to_dict()
    )

    all_clean = all(
        classifications.get(ticker) == "historically_corroborated_clean"
        for ticker in tickers
    )

    payload = {
        "schema_version": 1,
        "status": "V4_EXACT_COHORT_COLLAPSED_HISTORY_REVIEW_COMPLETE",
        "as_of": as_of,
        "candidate_tickers": sorted(tickers),
        "candidate_issuers": len(tickers),
        "collapsed_filing_rows": int(len(detail)),
        "historically_clean_filing_rows": clean_rows,
        "material_filing_rows": material_rows,
        "ambiguous_filing_rows": ambiguous_rows,
        "issuer_classifications": classifications,
        "historical_freeze_gate_pass": bool(
            all_clean and material_rows == 0 and ambiguous_rows == 0
        ),
        "identity_used_for_recovery": False,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
        "research_only": True,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_exact_collapsed_history_review"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_path = output_dir / "collapsed_historical_detail.csv"
    issuer_path = output_dir / "issuer_summary.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    by_ticker.to_csv(issuer_path, index=False)
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 EXACT-COHORT COLLAPSED HISTORICAL IDENTITY REVIEW")
    print(f"As of:                       {as_of}")
    print("Candidate tickers:           " + ",".join(sorted(tickers)))
    print(f"Collapsed filing rows:       {len(detail)}")
    print(f"Historically clean rows:     {clean_rows}")
    print(f"Material filing rows:        {material_rows}")
    print(f"Ambiguous filing rows:       {ambiguous_rows}")
    for ticker in sorted(tickers):
        print(
            f"{ticker:6s} classification:          "
            f"{classifications.get(ticker, 'missing')}"
        )
    print(
        f"Historical freeze gate:      "
        f"{'PASS' if payload['historical_freeze_gate_pass'] else 'REVIEW'}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Issuer summary:              {issuer_path}")
    print(f"Summary:                     {summary_path}")
    print("IDENTITY FACTS WERE VALIDATION ONLY; THEY WERE NOT RECOVERY INPUTS.")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
