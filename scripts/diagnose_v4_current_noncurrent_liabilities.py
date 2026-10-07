from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the Issue #30 V4 current+noncurrent validation cohort "
            "after identity validation. Read-only; no recovery is applied."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _accepted(frame: pd.DataFrame) -> pd.Series:
    source = frame.get("accepted_at", pd.Series(pd.NaT, index=frame.index))
    return pd.to_datetime(source, errors="coerce", utc=True).dt.tz_convert(None)


def _dates(frame: pd.DataFrame) -> pd.Series:
    source = frame.get("ddate_date", frame.get("ddate", pd.Series(pd.NaT, index=frame.index)))
    return pd.to_datetime(source, errors="coerce").dt.normalize()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    validation_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_current_noncurrent_validation"
    )
    validation_path = validation_dir / "current_noncurrent_identity_validation.csv"
    if not validation_path.exists():
        raise SystemExit(f"Missing V4 validation detail: {validation_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    candidates_path = v2["sec_candidates"]
    if not candidates_path.exists():
        raise SystemExit(f"Missing saved V2 SEC candidates: {candidates_path}")

    validation = pd.read_csv(validation_path, low_memory=False)
    candidates = pd.read_csv(candidates_path, low_memory=False)
    validation["ticker"] = validation["ticker"].astype(str).str.upper().str.strip()
    candidates["ticker"] = candidates["ticker"].astype(str).str.upper().str.strip()

    candidates["_accepted_at"] = _accepted(candidates)
    candidates["_period"] = _dates(candidates)
    cutoff = pd.Timestamp(args.as_of).normalize()

    rows: list[dict[str, object]] = []
    for row in validation.itertuples(index=False):
        ticker = str(row.ticker).upper()
        band = str(row.validation_band)
        context = pd.to_datetime(
            getattr(row, "context_instant", ""),
            errors="coerce",
        )
        frame = candidates.loc[candidates["ticker"].eq(ticker)].copy()
        frame = frame.loc[
            frame["_accepted_at"].notna()
            & frame["_accepted_at"].le(cutoff)
        ].copy()

        concept = frame.get("concept", pd.Series("", index=frame.index)).astype(str)
        assets = frame.loc[concept.eq("total_assets")].copy()
        equity = frame.loc[concept.eq("shareholders_equity")].copy()

        asset_periods = sorted(
            {
                value.date().isoformat()
                for value in assets["_period"].dropna()
            }
        )
        equity_periods = sorted(
            {
                value.date().isoformat()
                for value in equity["_period"].dropna()
            }
        )
        common_periods = sorted(set(asset_periods) & set(equity_periods))

        same_context_period = (
            context.date().isoformat()
            if pd.notna(context)
            else ""
        )
        has_assets_same_period = same_context_period in asset_periods
        has_equity_same_period = same_context_period in equity_periods

        if band in {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}:
            disposition = "clean_current_plus_noncurrent_candidate"
            next_action = "eligible_for_historical_pit_replay"
        elif band == "material_difference":
            disposition = "material_identity_mismatch"
            next_action = "reject_or_explain_before_any_replay"
        else:
            if not asset_periods and not equity_periods:
                disposition = "no_assets_or_equity_validation_evidence"
            elif not asset_periods:
                disposition = "missing_assets_validation_evidence"
            elif not equity_periods:
                disposition = "missing_equity_validation_evidence"
            elif not common_periods:
                disposition = "assets_equity_period_mismatch"
            elif not (has_assets_same_period and has_equity_same_period):
                disposition = "identity_evidence_not_at_candidate_instant"
            else:
                disposition = "ambiguous_same_context_identity_evidence"
            next_action = "targeted_evidence_review"

        rows.append(
            {
                "ticker": ticker,
                "cik": getattr(row, "cik", ""),
                "company_name": getattr(row, "company_name", ""),
                "current_plus_noncurrent": getattr(
                    row, "current_plus_noncurrent", pd.NA
                ),
                "candidate_context_instant": same_context_period,
                "validation_band": band,
                "assets_minus_equity_validation_value": getattr(
                    row, "assets_minus_equity_validation_value", pd.NA
                ),
                "absolute_relative_error": getattr(
                    row, "absolute_relative_error", pd.NA
                ),
                "assets_periods": "|".join(asset_periods),
                "equity_periods": "|".join(equity_periods),
                "common_assets_equity_periods": "|".join(common_periods),
                "assets_present_at_candidate_instant": has_assets_same_period,
                "equity_present_at_candidate_instant": has_equity_same_period,
                "disposition": disposition,
                "next_action": next_action,
            }
        )

    detail = pd.DataFrame(rows).sort_values(
        ["next_action", "disposition", "ticker"],
        kind="stable",
    ).reset_index(drop=True)

    summary = (
        detail.groupby(["disposition", "next_action"], as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(["rows", "disposition"], ascending=[False, True], kind="stable")
        .reset_index(drop=True)
    )

    clean = detail.loc[
        detail["next_action"].eq("eligible_for_historical_pit_replay")
    ].copy()

    payload = {
        "schema_version": 1,
        "status": "V4_CURRENT_NONCURRENT_DIAGNOSTIC_COMPLETE",
        "as_of": as_of,
        "cohort_rows": int(len(detail)),
        "clean_current_candidates": int(len(clean)),
        "material_mismatches": int(
            detail["disposition"].eq("material_identity_mismatch").sum()
        ),
        "targeted_evidence_review": int(
            detail["next_action"].eq("targeted_evidence_review").sum()
        ),
        "historical_replay_authorized_for_clean_subset_only": True,
        "assets_minus_equity_used_for_recovery": False,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
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
        / "liabilities_current_noncurrent_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_path = output_dir / "candidate_diagnostic_detail.csv"
    summary_path = output_dir / "candidate_diagnostic_summary.csv"
    clean_path = output_dir / "clean_historical_replay_cohort.csv"
    json_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)
    clean.to_csv(clean_path, index=False)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 CURRENT+NONCURRENT LIABILITIES DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Cohort rows:                 {len(detail)}")
    print(f"Clean replay candidates:     {len(clean)}")
    print(
        f"Material mismatches:         "
        f"{payload['material_mismatches']}"
    )
    print(
        f"Targeted evidence review:    "
        f"{payload['targeted_evidence_review']}"
    )
    print()
    for row in summary.itertuples(index=False):
        print(f"{int(row.rows):4d}  {row.disposition}  [{row.next_action}]")
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Clean replay cohort:         {clean_path}")
    print(f"Summary:                     {json_path}")
    print("ASSETS - EQUITY REMAINS VALIDATION ONLY.")
    print("V1/V2/V3 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
