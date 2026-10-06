from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


SAFE_BANDS = {"exact_match", "within_0_01_pct", "within_0_1_pct"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Filter the existing V3 historical identity corroboration to the "
            "exact-match V4 liabilities cohort. Validation-only; V1/V2/V3 remain read-only."
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
    corroboration_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
        / "current_candidate_identity_corroboration.csv"
    )

    if not cohort_path.exists():
        raise SystemExit(f"Missing V4 clean cohort: {cohort_path}")
    if not corroboration_path.exists():
        raise SystemExit(
            "Missing existing V3 historical identity corroboration: "
            f"{corroboration_path}\n"
            "Run scripts/corroborate_v3_current_liabilities_candidates_history.py "
            "against the historical SEC ZIP directory first."
        )

    cohort = pd.read_csv(cohort_path, low_memory=False)
    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    cohort = cohort.loc[
        cohort["validation_band"].astype(str).eq("exact_match")
    ].copy()
    tickers = set(cohort["ticker"])
    if not tickers:
        raise SystemExit("No exact-match V4 liabilities cohort found")

    detail = pd.read_csv(corroboration_path, low_memory=False)
    detail["ticker"] = detail["ticker"].astype(str).str.upper().str.strip()
    detail = detail.loc[detail["ticker"].isin(tickers)].copy()

    if detail.empty:
        raise SystemExit(
            "Existing historical corroboration contains no rows for "
            + ",".join(sorted(tickers))
        )

    filing_keys = [
        column
        for column in ("ticker", "cik", "adsh", "ddate_date", "identity_type", "equity_tag")
        if column in detail.columns
    ]
    unique = (
        detail.sort_values(filing_keys, kind="stable")
        .drop_duplicates(filing_keys, keep="last")
        .reset_index(drop=True)
    )

    issuer_rows: list[dict[str, object]] = []
    for ticker in sorted(tickers):
        group = unique.loc[unique["ticker"].eq(ticker)].copy()
        bands = group["validation_band"].astype(str)
        material = bands.eq("material_difference")
        safe = bands.isin(SAFE_BANDS)
        issuer_rows.append(
            {
                "ticker": ticker,
                "corroboration_rows": int(len(group)),
                "exact_matches": int(bands.eq("exact_match").sum()),
                "within_0_01_pct": int(bands.eq("within_0_01_pct").sum()),
                "within_0_1_pct": int(bands.eq("within_0_1_pct").sum()),
                "within_1_pct": int(bands.eq("within_1_pct").sum()),
                "material_rows": int(material.sum()),
                "safe_rows": int(safe.sum()),
                "safe_rate": float(safe.mean()) if len(group) else None,
                "max_absolute_relative_error": (
                    float(pd.to_numeric(
                        group["absolute_relative_error"], errors="coerce"
                    ).max())
                    if len(group)
                    else None
                ),
                "historical_corroboration_present": bool(len(group)),
                "issuer_freeze_gate_pass": bool(len(group) and not material.any()),
            }
        )

    issuer = pd.DataFrame(issuer_rows)
    all_material = int(issuer["material_rows"].sum())
    all_present = bool(issuer["historical_corroboration_present"].all())
    all_pass = bool(issuer["issuer_freeze_gate_pass"].all())

    payload = {
        "schema_version": 1,
        "status": "V4_EXACT_COHORT_EXISTING_HISTORICAL_IDENTITY_REVIEW_COMPLETE",
        "as_of": as_of,
        "candidate_tickers": sorted(tickers),
        "candidate_issuers": int(len(tickers)),
        "corroboration_rows": int(len(unique)),
        "issuers_with_corroboration": int(
            issuer["historical_corroboration_present"].sum()
        ),
        "exact_matches": int(
            unique["validation_band"].astype(str).eq("exact_match").sum()
        ),
        "within_0_01_pct": int(
            unique["validation_band"].astype(str).eq("within_0_01_pct").sum()
        ),
        "within_0_1_pct": int(
            unique["validation_band"].astype(str).eq("within_0_1_pct").sum()
        ),
        "within_1_pct": int(
            unique["validation_band"].astype(str).eq("within_1_pct").sum()
        ),
        "material_rows": all_material,
        "all_issuers_have_corroboration": all_present,
        "historical_freeze_gate_pass": bool(all_present and all_pass),
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
        / "liabilities_exact_existing_identity_review"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "historical_identity_detail.csv"
    issuer_path = output_dir / "issuer_summary.csv"
    summary_path = output_dir / "summary.json"

    unique.to_csv(detail_path, index=False)
    issuer.to_csv(issuer_path, index=False)
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 EXACT-COHORT EXISTING HISTORICAL IDENTITY REVIEW")
    print(f"As of:                       {as_of}")
    print("Candidate tickers:           " + ",".join(sorted(tickers)))
    print(f"Corroboration rows:          {payload['corroboration_rows']}")
    print(
        f"Issuers with corroboration:  "
        f"{payload['issuers_with_corroboration']}/{payload['candidate_issuers']}"
    )
    print(f"Exact matches:               {payload['exact_matches']}")
    print(f"Within 0.01%:                {payload['within_0_01_pct']}")
    print(f"Within 0.1%:                 {payload['within_0_1_pct']}")
    print(f"Within 1%:                   {payload['within_1_pct']}")
    print(f"Material rows:               {payload['material_rows']}")
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
