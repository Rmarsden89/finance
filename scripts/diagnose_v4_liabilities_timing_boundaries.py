from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths


TARGET_BUCKET = "timing_or_filing_availability_boundary"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the V4 liabilities residuals classified as timing or "
            "filing-availability boundaries. Research-only; no model mutation."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _accepted_naive(frame: pd.DataFrame) -> pd.Series:
    for column in ("accepted_at", "accepted", "accepted_datetime"):
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce")
            try:
                values = values.dt.tz_localize(None)
            except (TypeError, AttributeError):
                try:
                    values = values.dt.tz_convert(None)
                except (TypeError, AttributeError):
                    pass
            return values
    return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()
    cutoff = pd.Timestamp(args.as_of).normalize()

    classification_path = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_residual_classification"
        / "liabilities_residual_detail.csv"
    )
    if not classification_path.exists():
        raise SystemExit(
            f"Missing V4 liabilities classification detail: {classification_path}"
        )

    classified = pd.read_csv(classification_path, low_memory=False)
    classified["ticker"] = (
        classified["ticker"].astype(str).str.upper().str.strip()
    )
    timing = classified.loc[
        classified["v4_liabilities_bucket"].astype(str).eq(TARGET_BUCKET)
    ].copy()
    if timing.empty:
        raise SystemExit("No timing/filing-availability residuals found")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    candidate_path = v2["sec_candidates"]
    if not candidate_path.exists():
        raise SystemExit(f"Missing V2 SEC candidates: {candidate_path}")

    candidates = pd.read_csv(candidate_path, low_memory=False)
    candidates["ticker"] = (
        candidates["ticker"].astype(str).str.upper().str.strip()
    )
    candidates["_accepted_at"] = _accepted_naive(candidates)
    if "concept" not in candidates.columns:
        raise SystemExit("V2 SEC candidate artifact lacks concept column")

    target_tickers = set(timing["ticker"])
    liability = candidates.loc[
        candidates["ticker"].isin(target_tickers)
        & candidates["concept"].astype(str).eq("total_liabilities")
    ].copy()

    if "ddate_date" in liability.columns:
        liability["_period"] = pd.to_datetime(
            liability["ddate_date"], errors="coerce"
        ).dt.normalize()
    else:
        liability["_period"] = pd.NaT

    liability["decision_cutoff"] = cutoff
    liability["accepted_after_cutoff"] = (
        liability["_accepted_at"].notna()
        & liability["_accepted_at"].gt(cutoff)
    )
    liability["hours_after_cutoff"] = (
        liability["_accepted_at"] - cutoff
    ).dt.total_seconds() / 3600.0

    if "value" in liability.columns:
        liability["_value"] = pd.to_numeric(
            liability["value"], errors="coerce"
        )
    else:
        liability["_value"] = pd.NA

    detail_columns = [
        column
        for column in (
            "ticker",
            "concept",
            "source_tag",
            "value",
            "_value",
            "uom",
            "adsh",
            "ddate_date",
            "_period",
            "filed_date",
            "accepted_at",
            "_accepted_at",
            "decision_cutoff",
            "accepted_after_cutoff",
            "hours_after_cutoff",
            "qtrs",
            "form",
        )
        if column in liability.columns
    ]
    candidate_detail = liability[detail_columns].copy()

    rows: list[dict[str, object]] = []
    for ticker in sorted(target_tickers):
        class_row = timing.loc[timing["ticker"].eq(ticker)].iloc[0]
        group = liability.loc[liability["ticker"].eq(ticker)].copy()
        accepted = group["_accepted_at"].dropna().sort_values()
        after = accepted.loc[accepted.gt(cutoff)]
        before = accepted.loc[accepted.le(cutoff)]

        earliest_after = after.iloc[0] if len(after) else pd.NaT
        latest_before = before.iloc[-1] if len(before) else pd.NaT

        rows.append(
            {
                "ticker": ticker,
                "cik": class_row.get("cik"),
                "v2_classification": class_row.get("liabilities_classification"),
                "v2_recommended_action": class_row.get(
                    "liabilities_recommended_action"
                ),
                "candidate_acceptance_status": class_row.get(
                    "liabilities_candidate_acceptance_status"
                ),
                "discovery_statuses": class_row.get(
                    "liabilities_discovery_statuses"
                ),
                "discovery_accessions": class_row.get(
                    "liabilities_discovery_accessions"
                ),
                "candidate_rows": int(len(group)),
                "candidate_rows_pit_eligible_at_cutoff": int(
                    group["_accepted_at"].le(cutoff).fillna(False).sum()
                ),
                "candidate_rows_after_cutoff": int(
                    group["accepted_after_cutoff"].fillna(False).sum()
                ),
                "earliest_candidate_after_cutoff": (
                    earliest_after.isoformat()
                    if pd.notna(earliest_after)
                    else None
                ),
                "hours_to_earliest_candidate": (
                    float((earliest_after - cutoff).total_seconds() / 3600.0)
                    if pd.notna(earliest_after)
                    else None
                ),
                "latest_candidate_at_or_before_cutoff": (
                    latest_before.isoformat()
                    if pd.notna(latest_before)
                    else None
                ),
                "diagnostic_status": (
                    "accepted_after_decision_cutoff"
                    if len(after) and not len(before)
                    else (
                        "has_pit_eligible_candidate_in_artifact"
                        if len(before)
                        else "no_candidate_acceptance_timestamp"
                    )
                ),
            }
        )

    issuer_summary = pd.DataFrame(rows)

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_timing_boundary_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_path = output_dir / "timing_candidate_detail.csv"
    issuer_path = output_dir / "issuer_summary.csv"
    summary_path = output_dir / "summary.json"

    candidate_detail.to_csv(detail_path, index=False)
    issuer_summary.to_csv(issuer_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_LIABILITIES_TIMING_BOUNDARY_DIAGNOSTIC_COMPLETE",
        "as_of": as_of,
        "decision_cutoff": cutoff.isoformat(),
        "residual_tickers": sorted(target_tickers),
        "residual_count": int(len(target_tickers)),
        "accepted_after_cutoff_only": int(
            issuer_summary["diagnostic_status"]
            .eq("accepted_after_decision_cutoff")
            .sum()
        ),
        "has_pit_eligible_candidate_in_artifact": int(
            issuer_summary["diagnostic_status"]
            .eq("has_pit_eligible_candidate_in_artifact")
            .sum()
        ),
        "no_candidate_acceptance_timestamp": int(
            issuer_summary["diagnostic_status"]
            .eq("no_candidate_acceptance_timestamp")
            .sum()
        ),
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
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 LIABILITIES TIMING / FILING-AVAILABILITY DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Decision cutoff:             {cutoff.isoformat()}")
    print(f"Residual tickers:            {len(target_tickers)}")
    print()
    for row in issuer_summary.itertuples(index=False):
        print(
            f"{row.ticker:6s} status={row.diagnostic_status} "
            f"candidates={row.candidate_rows} "
            f"eligible={row.candidate_rows_pit_eligible_at_cutoff} "
            f"after={row.candidate_rows_after_cutoff} "
            f"hours_to_candidate={row.hours_to_earliest_candidate}"
        )
    print()
    print(
        f"Accepted-after-cutoff only:  "
        f"{payload['accepted_after_cutoff_only']}"
    )
    print(
        f"Has PIT-eligible candidate:  "
        f"{payload['has_pit_eligible_candidate_in_artifact']}"
    )
    print(
        f"No acceptance timestamp:     "
        f"{payload['no_candidate_acceptance_timestamp']}"
    )
    print(f"Candidate detail:            {detail_path}")
    print(f"Issuer summary:              {issuer_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
