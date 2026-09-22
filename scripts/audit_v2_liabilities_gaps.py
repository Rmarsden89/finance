from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.liabilities_audit import (
    build_same_context_liabilities_identities,
    classify_liabilities_gaps,
    liabilities_coverage_by_year,
    validate_liabilities_identity,
)
from finance.research.v2 import (
    resolve_v2_impact_artifact_paths,
    resolve_v2_liabilities_audit_paths,
    resolve_v2_sec_artifact_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify every V2 total-liabilities gap and quantify Financial "
            "Health coverage without changing data or scoring rules."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _sector_coverage(snapshot: pd.DataFrame) -> pd.DataFrame:
    grouping = next(
        (column for column in ("sector", "gics_sector", "industry") if column in snapshot),
        None,
    )
    if grouping is None:
        return pd.DataFrame(
            columns=[
                "grouping",
                "group",
                "rows",
                "positive_liabilities",
                "liabilities_coverage_pct",
            ]
        )
    frame = snapshot.copy()
    frame["_positive"] = pd.to_numeric(
        frame["total_liabilities"], errors="coerce"
    ).gt(0)
    frame["_group"] = frame[grouping].fillna("(missing)").astype(str)
    result = frame.groupby("_group", as_index=False).agg(
        rows=("ticker", "size"), positive_liabilities=("_positive", "sum")
    )
    result.insert(0, "grouping", grouping)
    result = result.rename(columns={"_group": "group"})
    result["liabilities_coverage_pct"] = (
        result["positive_liabilities"] / result["rows"]
    )
    return result


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(repo_root, args.as_of)
    impact = resolve_v2_impact_artifact_paths(repo_root, args.as_of)
    output = resolve_v2_liabilities_audit_paths(repo_root, args.as_of)

    if not v2["manifest"].exists():
        raise SystemExit(f"Missing completed V2 manifest: {v2['manifest']}")
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete before liabilities audit")
    if any(bool(value) for value in manifest.get("execution_capabilities", {}).values()):
        raise SystemExit("V2 manifest enables an execution capability")

    input_artifacts = manifest.get("input_artifacts", {})
    discovery_value = input_artifacts.get("discovery")
    cache_value = input_artifacts.get("SEC cache")
    if not discovery_value or not cache_value:
        raise SystemExit("V2 manifest is missing discovery or SEC cache input")
    discovery = Path(discovery_value)
    cache_dir = Path(cache_value)
    required = {
        "snapshot": v2["current_snapshot"],
        "discovery": discovery,
        "candidate audit": v2["sec_candidate_audit"],
        "candidates": v2["sec_candidates"],
        "merge audit": v2["sec_merge_audit"],
        "challenger scored panel": impact["challenger_scored"],
        "SEC cache": cache_dir,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing liabilities audit input(s):\n  " + "\n  ".join(missing))

    snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    scored = pd.read_csv(impact["challenger_scored"], low_memory=False)
    detail, summary = classify_liabilities_gaps(
        snapshot=snapshot,
        discovery=pd.read_csv(discovery, low_memory=False),
        candidate_audit=pd.read_csv(v2["sec_candidate_audit"], low_memory=False),
        candidates=pd.read_csv(v2["sec_candidates"], low_memory=False),
        merge_audit=pd.read_csv(v2["sec_merge_audit"], low_memory=False),
        scored=scored,
        cache_dir=cache_dir,
        as_of=args.as_of,
    )

    classification = (
        detail.groupby(["recommended_action", "classification"], as_index=False)
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(["recommended_action", "classification"], kind="stable")
    )
    by_year = liabilities_coverage_by_year(scored)
    by_sector = _sector_coverage(snapshot)
    identities = build_same_context_liabilities_identities(
        pd.read_csv(v2["sec_candidates"], low_memory=False),
        as_of=args.as_of,
    )
    gap_tickers = set(
        detail.loc[
            detail["recommended_action"].eq("research_identity_candidate"),
            "ticker",
        ]
    )
    identity_gaps = identities.loc[identities["ticker"].isin(gap_tickers)].copy()
    identity_validation, identity_summary = validate_liabilities_identity(
        pd.read_csv(v2["sec_candidates"], low_memory=False),
        as_of=args.as_of,
    )
    alternate = detail.loc[
        detail["recommended_action"].eq("research_alternate_tag")
    ].copy()
    alternate_summary = (
        alternate.groupby(
            ["current_filing_liability_tags", "current_filing_total_like_tags"],
            dropna=False,
            as_index=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values("rows", ascending=False, kind="stable")
    )

    output["audit_dir"].mkdir(parents=True, exist_ok=True)
    detail.to_csv(output["detail"], index=False)
    classification.to_csv(output["classification_summary"], index=False)
    by_year.to_csv(output["coverage_by_year"], index=False)
    by_sector.to_csv(output["coverage_by_sector"], index=False)
    identity_gaps.to_csv(output["identity_gap_candidates"], index=False)
    identity_validation.to_csv(output["identity_validation"], index=False)
    identity_summary.to_csv(output["identity_validation_summary"], index=False)
    alternate_summary.to_csv(output["alternate_tag_summary"], index=False)
    output["summary"].write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    direct_inputs = [
        v2["manifest"],
        v2["current_snapshot"],
        discovery,
        v2["sec_candidate_audit"],
        v2["sec_candidates"],
        v2["sec_merge_audit"],
        impact["challenger_scored"],
    ]
    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "source_research_fingerprint_bundle": manifest.get(
            "input_fingerprints", {}
        ).get("bundle_sha256"),
        "direct_inputs": fingerprint_files(root=repo_root, paths=direct_inputs),
        "audit_code": git_provenance(repo_root),
    }
    output["input_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V2 TOTAL LIABILITIES GAP AUDIT")
    print(f"Positive liabilities:       {summary.positive_liabilities}/{summary.universe_rows}")
    print(f"Residual rows:              {summary.residual_rows}")
    print(f"Health eligible:            {summary.health_eligible}/{summary.universe_rows}")
    print(
        "Health factor counts 0/1/2/3: "
        f"{summary.health_factor_count_0}/"
        f"{summary.health_factor_count_1}/"
        f"{summary.health_factor_count_2}/"
        f"{summary.health_factor_count_3}"
    )
    print(f"Targeted SEC refresh:       {summary.targeted_sec_refresh}")
    print(f"Invalid values:             {summary.investigate_invalid_value}")
    print(f"Candidate-selection bugs:   {summary.candidate_not_selected}")
    print(f"Identity candidates:        {summary.research_identity_candidate}")
    print(f"Alternate-tag research:     {summary.research_alternate_tag}")
    print(f"No supported current fact:  {summary.documented_no_supported_fact}")
    print(f"Identity validation rows:   {len(identity_validation)}")
    print(f"Detail:                     {output['detail']}")
    print(f"Summary:                    {output['summary']}")
    print("V1 data, V2 data, and scoring rules were NOT modified.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
