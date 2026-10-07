from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Historically corroborate the exact-match V4 liabilities cohort "
            "against filing-level direct Liabilities controls already produced "
            "by the frozen V3 historical validation."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _normalize_cik(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "cik" in result.columns:
        result["cik"] = pd.to_numeric(
            result["cik"], errors="coerce"
        ).astype("Int64")
    return result


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
    if not cohort_path.exists():
        raise SystemExit(f"Missing V4 clean replay cohort: {cohort_path}")

    cohort = _normalize_cik(pd.read_csv(cohort_path, low_memory=False))
    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    cohort = cohort.loc[
        cohort["validation_band"].astype(str).eq("exact_match")
    ].copy()
    if cohort.empty:
        raise SystemExit("No exact-match V4 liabilities cohort rows found")

    exact_ciks = set(cohort["cik"].dropna().astype(int))

    v3_hist = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
    )
    overlap_path = v3_hist / "filing_overlap_validation.csv"
    replay_path = v3_hist / "pit_replay_detail.csv"
    if not overlap_path.exists():
        raise SystemExit(f"Missing historical filing overlap: {overlap_path}")
    if not replay_path.exists():
        raise SystemExit(f"Missing historical PIT replay: {replay_path}")

    overlap = _normalize_cik(pd.read_csv(overlap_path, low_memory=False))
    replay = _normalize_cik(pd.read_csv(replay_path, low_memory=False))
    overlap = overlap.loc[overlap["cik"].isin(exact_ciks)].copy()
    replay = replay.loc[replay["cik"].isin(exact_ciks)].copy()

    if "ticker" in replay.columns:
        replay["ticker"] = replay["ticker"].astype(str).str.upper().str.strip()

    join_keys = ["cik"]
    if {"adsh", "selected_accession"}.issubset(
        set(overlap.columns) | set(replay.columns)
    ):
        pass

    controls = overlap.copy()
    if "ddate_date" in controls.columns:
        controls["_period"] = pd.to_datetime(
            controls["ddate_date"], errors="coerce"
        ).dt.normalize()
    else:
        controls["_period"] = pd.NaT

    if "adsh" in controls.columns:
        controls["_accession"] = controls["adsh"].astype(str).str.strip()
    else:
        controls["_accession"] = ""

    replay["_period"] = pd.to_datetime(
        replay.get("selected_period_date"), errors="coerce"
    ).dt.normalize()
    replay["_accession"] = (
        replay.get("selected_accession", pd.Series("", index=replay.index))
        .astype(str)
        .str.strip()
    )

    control_columns = [
        "cik",
        "_accession",
        "_period",
        "validation_band",
        "absolute_relative_error",
    ]
    available_control_columns = [
        column for column in control_columns if column in controls.columns
    ]
    control_map = (
        controls[available_control_columns]
        .sort_values(
            ["cik", "_accession", "_period"],
            kind="stable",
        )
        .drop_duplicates(
            ["cik", "_accession", "_period"],
            keep="last",
        )
    )

    detail = replay.merge(
        control_map,
        on=["cik", "_accession", "_period"],
        how="left",
        validate="many_to_one",
    )

    detail["historical_control_status"] = detail[
        "validation_band"
    ].fillna("no_direct_control")
    detail["historical_material_mismatch"] = detail[
        "validation_band"
    ].astype(str).eq("material_difference")

    unique_selected = (
        detail[
            [
                "ticker",
                "cik",
                "_accession",
                "_period",
                "validation_band",
                "absolute_relative_error",
                "historical_control_status",
                "historical_material_mismatch",
            ]
        ]
        .drop_duplicates(
            ["ticker", "cik", "_accession", "_period"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    rows: list[dict[str, object]] = []
    for ticker, group in detail.groupby("ticker", dropna=False):
        unique_group = unique_selected.loc[
            unique_selected["ticker"].eq(ticker)
        ]
        controlled = group["validation_band"].notna()
        unique_controlled = unique_group["validation_band"].notna()
        rows.append(
            {
                "ticker": ticker,
                "cik": int(group["cik"].dropna().iloc[0]),
                "pit_replay_rows": int(len(group)),
                "pit_rows_with_direct_control": int(controlled.sum()),
                "pit_direct_control_rate": float(controlled.mean()),
                "unique_selected_filings": int(len(unique_group)),
                "unique_filings_with_direct_control": int(
                    unique_controlled.sum()
                ),
                "unique_exact_matches": int(
                    unique_group["validation_band"].astype(str).eq(
                        "exact_match"
                    ).sum()
                ),
                "unique_within_0_01_pct": int(
                    unique_group["validation_band"].astype(str).eq(
                        "within_0_01_pct"
                    ).sum()
                ),
                "unique_within_0_1_pct": int(
                    unique_group["validation_band"].astype(str).eq(
                        "within_0_1_pct"
                    ).sum()
                ),
                "unique_within_1_pct": int(
                    unique_group["validation_band"].astype(str).eq(
                        "within_1_pct"
                    ).sum()
                ),
                "unique_material_differences": int(
                    unique_group["historical_material_mismatch"].sum()
                ),
                "max_controlled_absolute_relative_error": (
                    float(
                        pd.to_numeric(
                            unique_group["absolute_relative_error"],
                            errors="coerce",
                        ).max()
                    )
                    if unique_controlled.any()
                    else None
                ),
            }
        )

    issuer_summary = pd.DataFrame(rows).sort_values(
        "ticker", kind="stable"
    ).reset_index(drop=True)

    material = unique_selected.loc[
        unique_selected["historical_material_mismatch"]
    ].copy()

    controlled_unique = unique_selected.loc[
        unique_selected["validation_band"].notna()
    ].copy()

    payload = {
        "schema_version": 1,
        "status": "V4_EXACT_COHORT_HISTORICAL_CORROBORATION_COMPLETE",
        "as_of": as_of,
        "candidate_tickers": sorted(cohort["ticker"].tolist()),
        "candidate_issuers": int(len(cohort)),
        "pit_replay_rows": int(len(detail)),
        "unique_selected_filings": int(len(unique_selected)),
        "unique_filings_with_direct_control": int(len(controlled_unique)),
        "unique_exact_matches": int(
            controlled_unique["validation_band"].astype(str).eq(
                "exact_match"
            ).sum()
        ),
        "unique_within_0_01_pct": int(
            controlled_unique["validation_band"].astype(str).eq(
                "within_0_01_pct"
            ).sum()
        ),
        "unique_within_0_1_pct": int(
            controlled_unique["validation_band"].astype(str).eq(
                "within_0_1_pct"
            ).sum()
        ),
        "unique_within_1_pct": int(
            controlled_unique["validation_band"].astype(str).eq(
                "within_1_pct"
            ).sum()
        ),
        "unique_material_differences": int(len(material)),
        "controlled_unique_filing_rate": (
            float(len(controlled_unique) / len(unique_selected))
            if len(unique_selected)
            else None
        ),
        "historical_freeze_gate_pass": bool(
            len(material) == 0 and len(controlled_unique) > 0
        ),
        "assets_minus_equity_used_for_recovery": False,
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
        / "liabilities_exact_historical_corroboration"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    issuer_path = output_dir / "issuer_summary.csv"
    filing_path = output_dir / "unique_selected_filing_controls.csv"
    material_path = output_dir / "material_mismatches.csv"
    summary_path = output_dir / "summary.json"

    issuer_summary.to_csv(issuer_path, index=False)
    unique_selected.to_csv(filing_path, index=False)
    material.to_csv(material_path, index=False)
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 EXACT-COHORT HISTORICAL LIABILITIES CORROBORATION")
    print(f"As of:                       {as_of}")
    print(
        "Candidate tickers:           "
        + ",".join(payload["candidate_tickers"])
    )
    print(f"PIT replay rows:              {payload['pit_replay_rows']}")
    print(
        f"Unique selected filings:     "
        f"{payload['unique_selected_filings']}"
    )
    print(
        f"Filings with direct control: "
        f"{payload['unique_filings_with_direct_control']}"
    )
    print(f"Exact matches:               {payload['unique_exact_matches']}")
    print(f"Within 0.01%:                {payload['unique_within_0_01_pct']}")
    print(f"Within 0.1%:                 {payload['unique_within_0_1_pct']}")
    print(f"Within 1%:                   {payload['unique_within_1_pct']}")
    print(
        f"Material differences:        "
        f"{payload['unique_material_differences']}"
    )
    print(
        f"Controlled filing rate:      "
        f"{payload['controlled_unique_filing_rate']}"
    )
    print(
        f"Historical freeze gate:      "
        f"{'PASS' if payload['historical_freeze_gate_pass'] else 'REVIEW'}"
    )
    print(f"Issuer summary:              {issuer_path}")
    print(f"Filing controls:             {filing_path}")
    print(f"Material mismatches:         {material_path}")
    print(f"Summary:                     {summary_path}")
    print("DIRECT LIABILITIES CONTROLS WERE VALIDATION ONLY.")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
