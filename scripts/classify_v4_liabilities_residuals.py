from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify post-V3 total_liabilities residuals for Issue #30 using "
            "only frozen V2/V3 evidence. This command does not change V1/V2/V3."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _normalize_ticker(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "ticker" in result.columns:
        result["ticker"] = result["ticker"].astype(str).str.upper().str.strip()
    return result


def _load_optional(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return _normalize_ticker(pd.read_csv(path, low_memory=False))


def _one_row_per_ticker(frame: pd.DataFrame, *, source: str) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        return pd.DataFrame(columns=["ticker"])

    keep = [
        column
        for column in (
            "ticker",
            "status",
            "reason",
            "context_instant",
            "direct_value",
            "current_value",
            "noncurrent_value",
            "current_plus_noncurrent",
            "total_like_tags",
            "other_liability_tags",
            "original_classification",
            "original_recommended_action",
        )
        if column in frame.columns
    ]
    result = (
        frame[keep]
        .sort_values("ticker", kind="stable")
        .drop_duplicates("ticker", keep="first")
        .copy()
    )
    return result.rename(
        columns={
            column: f"{source}_{column}"
            for column in result.columns
            if column != "ticker"
        }
    )


def _first_nonblank(row: pd.Series, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column, "")
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _classify(row: pd.Series) -> tuple[str, str, str]:
    raw_status = _first_nonblank(
        row,
        (
            "identity_raw_status",
            "no_supported_raw_status",
            "alternate_raw_status",
        ),
    )
    v2_class = str(row.get("liabilities_classification", "") or "").strip()
    v2_action = str(row.get("liabilities_recommended_action", "") or "").strip()

    if raw_status == "current_plus_noncurrent_candidate":
        return (
            "current_plus_noncurrent_outside_frozen_v3_scope",
            "validate_broader_current_plus_noncurrent_rule",
            "Strongest SEC-native V4 candidate: V3 found the exact construction, "
            "but the issuer was not in the frozen 21-CIK rule.",
        )

    if raw_status == "raw_direct_liabilities_candidate":
        return (
            "raw_direct_liabilities_not_promoted_by_prior_canonical_path",
            "validate_raw_direct_liabilities_rule",
            "Direct reported Liabilities exists in raw filing evidence; determine "
            "why the canonical V2 path missed it and whether a V4 raw-filing rule "
            "can recover it safely.",
        )

    if raw_status == "total_like_only":
        return (
            "liabilities_plus_equity_total_only",
            "reject_as_direct_recovery_unless_new_semantics",
            "Only a liabilities-plus-equity total was found. This is not "
            "semantically equivalent to total liabilities.",
        )

    if raw_status == "alternate_tags_only":
        tags = _first_nonblank(
            row,
            (
                "alternate_raw_other_liability_tags",
                "identity_raw_other_liability_tags",
                "no_supported_raw_other_liability_tags",
                "liabilities_current_filing_liability_tags",
            ),
        )
        return (
            "alternate_or_custom_liability_tags",
            "research_custom_taxonomy_mapping",
            (
                "Liability-related tags exist but do not satisfy the frozen "
                "direct/current+noncurrent rules."
                + (f" Observed tags: {tags}" if tags else "")
            ),
        )

    if raw_status == "no_eligible_undimensioned_liability_evidence":
        return (
            "no_eligible_undimensioned_raw_liability_evidence",
            "inspect_dimensions_or_new_source_class",
            "Raw filing evidence exists, but no PIT-eligible undimensioned USD "
            "liability evidence survived the strict selector.",
        )

    if v2_action == "research_identity_candidate" or (
        v2_class == "same_context_assets_minus_equity_candidate"
    ):
        return (
            "assets_minus_equity_identity_candidate",
            "retain_rejected_or_revalidate_narrowly",
            "Assets - Equity was previously identified but rejected as a blanket "
            "recovery rule. Any V4 use requires a new narrow semantic validation.",
        )

    if v2_action == "research_alternate_tag" or (
        v2_class == "unsupported_alternate_liability_tag_present"
    ):
        return (
            "alternate_or_custom_liability_tags_without_raw_v3_resolution",
            "research_custom_taxonomy_mapping",
            "V2 identified alternate liability tags, but existing V3 raw evidence "
            "did not produce a frozen-rule candidate.",
        )

    if v2_class in {
        "no_new_supported_filing",
        "liabilities_candidate_not_pit_eligible",
    }:
        return (
            "timing_or_filing_availability_boundary",
            "retain_fail_closed_or_validate_new_pit_source",
            "The residual is constrained by point-in-time filing availability, "
            "not just taxonomy coverage.",
        )

    if v2_class in {
        "no_current_discovery",
        "current_discovery_incomplete",
        "cached_filing_missing_accession",
        "missing_companyfacts_cache",
    }:
        return (
            "data_retrieval_or_identity_gap",
            "recheck_current_pipeline_before_new_rule",
            "The older audit classified this as a retrieval/cache/discovery issue. "
            "Confirm whether it remains a true post-V3 residual before proposing "
            "new accounting semantics.",
        )

    if v2_class == "no_supported_current_liabilities_fact":
        return (
            "no_supported_current_liabilities_fact",
            "inspect_raw_dimensions_custom_tags_or_new_source",
            "Existing supported SEC concepts did not provide a usable current "
            "liabilities value.",
        )

    return (
        "unclassified_existing_evidence",
        "manual_evidence_review",
        "Existing evidence is insufficient to place this residual into a stronger "
        "deterministic V4 research bucket.",
    )


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    v4_base = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "post_v3_gap_inventory"
    )
    residual_path = v4_base / "post_v3_residual_gap_inventory.csv"
    summary_path = v4_base / "summary.json"

    if not residual_path.exists():
        raise SystemExit(f"Missing frozen post-V3 residual inventory: {residual_path}")
    if not summary_path.exists():
        raise SystemExit(f"Missing frozen V4 baseline summary: {summary_path}")

    baseline = json.loads(summary_path.read_text(encoding="utf-8"))
    if baseline.get("status") != "V4_BASELINE_FROZEN":
        raise SystemExit("V4 baseline must be frozen before liabilities classification")

    residual = _normalize_ticker(pd.read_csv(residual_path, low_memory=False))
    liabilities = residual.loc[
        residual["post_v3_liabilities_gap"].fillna(False).astype(bool)
    ].copy()

    if len(liabilities) != int(baseline.get("post_v3_liabilities_gaps", -1)):
        raise SystemExit(
            "Residual liabilities count does not match frozen V4 baseline: "
            f"{len(liabilities)} != {baseline.get('post_v3_liabilities_gaps')}"
        )

    v3_base = root / "reports" / "v3" / "data_sources" / as_of
    raw_sources = {
        "identity_raw": (
            v3_base
            / "liabilities_identity_raw_strict"
            / "raw_sec_detail.csv"
        ),
        "no_supported_raw": (
            v3_base
            / "liabilities_no_supported_current"
            / "raw_sec_detail.csv"
        ),
        "alternate_raw": (
            v3_base
            / "liabilities_alternate_tags"
            / "alternate_tag_detail.csv"
        ),
    }

    detail = liabilities.copy()
    for name, path in raw_sources.items():
        source = _one_row_per_ticker(_load_optional(path), source=name)
        if not source.empty:
            detail = detail.merge(
                source,
                on="ticker",
                how="left",
                validate="one_to_one",
            )

    classified = detail.apply(_classify, axis=1, result_type="expand")
    classified.columns = [
        "v4_liabilities_bucket",
        "v4_recommended_track",
        "v4_rationale",
    ]
    detail = pd.concat([detail, classified], axis=1)

    summary = (
        detail.groupby(
            ["v4_liabilities_bucket", "v4_recommended_track"],
            dropna=False,
            as_index=False,
        )
        .agg(
            rows=("ticker", "size"),
            tickers=("ticker", "nunique"),
        )
        .sort_values(
            ["rows", "v4_liabilities_bucket"],
            ascending=[False, True],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    raw_status_columns = [
        column
        for column in (
            "identity_raw_status",
            "no_supported_raw_status",
            "alternate_raw_status",
        )
        if column in detail.columns
    ]
    raw_status_rows: list[dict[str, object]] = []
    for column in raw_status_columns:
        counts = (
            detail[column]
            .fillna("")
            .astype(str)
            .replace({"nan": ""})
            .value_counts(dropna=False)
        )
        for status, count in counts.items():
            if not status:
                continue
            raw_status_rows.append(
                {
                    "source": column.removesuffix("_status"),
                    "status": status,
                    "rows": int(count),
                }
            )
    raw_status_summary = pd.DataFrame(raw_status_rows)

    high_priority = detail.loc[
        detail["v4_liabilities_bucket"].isin(
            {
                "current_plus_noncurrent_outside_frozen_v3_scope",
                "raw_direct_liabilities_not_promoted_by_prior_canonical_path",
            }
        )
    ].copy()

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_residual_classification"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_path = output_dir / "liabilities_residual_detail.csv"
    summary_csv = output_dir / "liabilities_residual_summary.csv"
    raw_summary_csv = output_dir / "raw_evidence_status_summary.csv"
    priority_path = output_dir / "priority_v4_liabilities_candidates.csv"
    json_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    summary.to_csv(summary_csv, index=False)
    raw_status_summary.to_csv(raw_summary_csv, index=False)
    high_priority.to_csv(priority_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_LIABILITIES_RESIDUAL_CLASSIFICATION_COMPLETE",
        "as_of": as_of,
        "universe_rows": int(baseline["universe_rows"]),
        "post_v3_liabilities_residuals": int(len(detail)),
        "classified_rows": int(detail["v4_liabilities_bucket"].notna().sum()),
        "high_priority_sec_native_candidates": int(len(high_priority)),
        "bucket_counts": {
            str(row.v4_liabilities_bucket): int(row.rows)
            for row in summary.itertuples(index=False)
        },
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
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 LIABILITIES RESIDUAL CLASSIFICATION")
    print(f"As of:                       {as_of}")
    print(f"Post-V3 liabilities gaps:    {len(detail)}")
    print(f"Classification buckets:      {len(summary)}")
    print(
        f"High-priority SEC-native:    "
        f"{payload['high_priority_sec_native_candidates']}"
    )
    print()
    for row in summary.itertuples(index=False):
        print(
            f"{int(row.rows):4d}  {row.v4_liabilities_bucket}  "
            f"[{row.v4_recommended_track}]"
        )
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Bucket summary:              {summary_csv}")
    print(f"Raw evidence statuses:       {raw_summary_csv}")
    print(f"Priority candidates:         {priority_path}")
    print(f"Summary:                     {json_path}")
    print("V1/V2/V3 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
