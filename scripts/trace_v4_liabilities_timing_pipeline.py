from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import (
    resolve_v2_liabilities_audit_paths,
    resolve_v2_sec_artifact_paths,
)


TARGET_BUCKET = "timing_or_filing_availability_boundary"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace V4 timing-boundary liabilities residuals back through the "
            "V2 liabilities audit and SEC candidate audit."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, low_memory=False)
    if "ticker" in frame.columns:
        frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    return frame


def _compact_values(frame: pd.DataFrame, ticker: str, columns: list[str]) -> dict[str, str]:
    if frame.empty or "ticker" not in frame.columns:
        return {column: "" for column in columns}
    group = frame.loc[frame["ticker"].eq(ticker)].copy()
    out: dict[str, str] = {}
    for column in columns:
        if column not in group.columns or group.empty:
            out[column] = ""
            continue
        values = (
            group[column]
            .dropna()
            .astype(str)
            .map(str.strip)
        )
        values = [value for value in values if value and value.lower() != "nan"]
        out[column] = "|".join(dict.fromkeys(values))
    return out


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

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
        raise SystemExit(f"Missing V4 classification detail: {classification_path}")

    classified = _load(classification_path)
    timing = classified.loc[
        classified["v4_liabilities_bucket"].astype(str).eq(TARGET_BUCKET)
    ].copy()
    if timing.empty:
        raise SystemExit("No timing-boundary liabilities residuals found")

    v2_audit_paths = resolve_v2_liabilities_audit_paths(root, args.as_of)
    v2_sec_paths = resolve_v2_sec_artifact_paths(root, args.as_of)

    audit = _load(v2_audit_paths["detail"])
    candidate_audit = _load(v2_sec_paths["sec_candidate_audit"])
    candidates = _load(v2_sec_paths["sec_candidates"])

    rows: list[dict[str, object]] = []
    for ticker in sorted(timing["ticker"].unique()):
        class_row = timing.loc[timing["ticker"].eq(ticker)].iloc[0]
        audit_vals = _compact_values(
            audit,
            ticker,
            [
                "classification",
                "recommended_action",
                "discovery_statuses",
                "discovery_accessions",
                "companyfacts_cache_present",
                "candidate_audit_statuses",
                "liabilities_merge_statuses",
                "current_liabilities_candidate_present",
                "pit_eligible_liabilities_candidate_present",
                "liabilities_candidate_acceptance_status",
                "current_filing_liability_tags",
                "current_filing_total_like_tags",
            ],
        )
        cand_audit_vals = _compact_values(
            candidate_audit,
            ticker,
            ["status", "reason", "concept", "source_tag", "adsh", "accepted_at"],
        )

        liability_candidates = pd.DataFrame()
        if not candidates.empty and {"ticker", "concept"}.issubset(candidates.columns):
            liability_candidates = candidates.loc[
                candidates["ticker"].eq(ticker)
                & candidates["concept"].astype(str).eq("total_liabilities")
            ].copy()

        classification = str(
            class_row.get("liabilities_classification", "")
        ).strip()
        discovery = audit_vals.get("discovery_statuses", "")
        candidate_status = audit_vals.get(
            "liabilities_candidate_acceptance_status", ""
        )

        if classification == "no_new_supported_filing":
            conclusion = "documented_no_supported_new_filing"
        elif classification == "liabilities_candidate_not_pit_eligible":
            conclusion = "candidate_present_but_not_pit_eligible"
        elif "error" in discovery.lower() or "partial" in discovery.lower():
            conclusion = "discovery_or_retrieval_incomplete"
        elif len(liability_candidates):
            conclusion = "candidate_exists_requires_manual_trace"
        else:
            conclusion = "no_supported_liabilities_candidate_in_v2_artifacts"

        rows.append(
            {
                "ticker": ticker,
                "cik": class_row.get("cik"),
                "v2_classification": classification,
                "v2_recommended_action": class_row.get(
                    "liabilities_recommended_action", ""
                ),
                "discovery_statuses": discovery,
                "discovery_accessions": audit_vals.get(
                    "discovery_accessions", ""
                ),
                "companyfacts_cache_present": audit_vals.get(
                    "companyfacts_cache_present", ""
                ),
                "candidate_audit_statuses": audit_vals.get(
                    "candidate_audit_statuses", ""
                ),
                "candidate_audit_detail_statuses": cand_audit_vals.get(
                    "status", ""
                ),
                "candidate_audit_reasons": cand_audit_vals.get("reason", ""),
                "liabilities_merge_statuses": audit_vals.get(
                    "liabilities_merge_statuses", ""
                ),
                "candidate_acceptance_status": candidate_status,
                "liability_candidate_rows": int(len(liability_candidates)),
                "current_filing_liability_tags": audit_vals.get(
                    "current_filing_liability_tags", ""
                ),
                "current_filing_total_like_tags": audit_vals.get(
                    "current_filing_total_like_tags", ""
                ),
                "diagnostic_conclusion": conclusion,
            }
        )

    detail = pd.DataFrame(rows)

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_timing_pipeline_trace"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "pipeline_trace.csv"
    summary_path = output_dir / "summary.json"
    detail.to_csv(detail_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_LIABILITIES_TIMING_PIPELINE_TRACE_COMPLETE",
        "as_of": as_of,
        "residual_count": int(len(detail)),
        "conclusion_counts": {
            str(key): int(value)
            for key, value in detail["diagnostic_conclusion"]
            .value_counts()
            .to_dict()
            .items()
        },
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 LIABILITIES TIMING-BOUNDARY PIPELINE TRACE")
    print(f"As of:                       {as_of}")
    print(f"Residual tickers:            {len(detail)}")
    print()
    for row in detail.itertuples(index=False):
        print(
            f"{row.ticker:6s} class={row.v2_classification} "
            f"discovery={row.discovery_statuses or '-'} "
            f"candidate_rows={row.liability_candidate_rows} "
            f"conclusion={row.diagnostic_conclusion}"
        )
        if row.current_filing_liability_tags:
            print(
                f"       liability_tags={row.current_filing_liability_tags}"
            )
        if row.current_filing_total_like_tags:
            print(
                f"       total_like_tags={row.current_filing_total_like_tags}"
            )
        if row.candidate_audit_reasons:
            print(
                f"       candidate_audit_reasons={row.candidate_audit_reasons}"
            )
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
