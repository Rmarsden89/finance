from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_promotion_criteria import (
    TTM_CHALLENGER,
    TTM_PROMOTION_THRESHOLDS,
)
from finance.research.ttm_shadow import (
    append_shadow_ledger,
    build_current_ttm_challenger,
    build_top10_comparison,
    build_v2_decision_payload,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_shadow_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one research-only weekly shadow observation for the frozen "
            "V2 TTM challenger. This command has no broker/order capability."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--pitindex-data", type=Path, default=None)
    parser.add_argument(
        "--v1-decision",
        type=Path,
        help=(
            "Saved V1 shadow_decision.json. Default: "
            "reports/shadow/<as-of>/shadow_decision.json."
        ),
    )
    parser.add_argument(
        "--market-snapshot",
        type=Path,
        help=(
            "Saved normalized market snapshot. Default: "
            "reports/shadow/<as-of>/robinhood_market_snapshot_normalized.csv."
        ),
    )
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current"),
    )
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _run(label: str, command: list[str], *, root: Path) -> None:
    print()
    print(f"=== {label} ===", flush=True)
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=root, check=True)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    python = sys.executable
    pitindex = (
        args.pitindex_data.resolve()
        if args.pitindex_data is not None
        else (root.parent / "pitindex" / "pitindex" / "data").resolve()
    )
    v1_decision_path = _resolve(
        root,
        args.v1_decision
        or Path("reports")
        / "shadow"
        / args.as_of.isoformat()
        / "shadow_decision.json",
    )
    market_path = _resolve(
        root,
        args.market_snapshot
        or Path("reports")
        / "shadow"
        / args.as_of.isoformat()
        / "robinhood_market_snapshot_normalized.csv",
    )
    discovery = _resolve(root, args.discovery)
    cache_dir = _resolve(root, args.cache_dir)

    initial_required = {
        "saved V1 decision": v1_decision_path,
        "normalized market snapshot": market_path,
        "SEC discovery": discovery,
        "SEC current cache": cache_dir,
        "PITIndex": pitindex,
    }
    missing = [
        f"{name}: {path}"
        for name, path in initial_required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing V2 shadow input(s):\n  " + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit("V2 shadow requires a clean tracked worktree")

    v1_decision = _read_json(v1_decision_path)
    v1_hash = str(v1_decision.get("decision_hash") or "").strip()
    if not v1_hash:
        raise SystemExit("Saved V1 decision is missing decision_hash")
    v1_as_of = str(v1_decision.get("as_of") or "")
    if v1_as_of != args.as_of.isoformat():
        raise SystemExit(
            "Saved V1 decision as_of does not match requested shadow date: "
            f"{v1_as_of} != {args.as_of.isoformat()}"
        )

    shadow = resolve_v2_shadow_artifact_paths(root, args.as_of)
    if shadow["shadow_dir"].exists():
        raise SystemExit(
            "V2 shadow output already exists for this date; observations are "
            f"immutable: {shadow['shadow_dir']}"
        )

    data_baseline_id = f"v1_decision:{v1_hash}"

    _run(
        "Build weekly V2 SEC research state",
        [
            python,
            "scripts/run_v2_sec_research.py",
            "--as-of",
            args.as_of.isoformat(),
            "--data-baseline-id",
            data_baseline_id,
            "--pitindex-data",
            str(pitindex),
            "--discovery",
            str(discovery),
            "--cache-dir",
            str(cache_dir),
            "--market-snapshot",
            str(market_path),
        ],
        root=root,
    )

    _run(
        "Overlay current TTM duration evidence",
        [
            python,
            "scripts/build_v2_current_ttm_duration_overlay.py",
            "--as-of",
            args.as_of.isoformat(),
            "--discovery",
            str(discovery),
            "--cache-dir",
            str(cache_dir),
        ],
        root=root,
    )
    _run(
        "Reconstruct current PIT-visible TTM quarters",
        [
            python,
            "scripts/audit_v2_ttm_reconstruction.py",
            "--as-of",
            args.as_of.isoformat(),
            "--use-current-duration-overlay",
        ],
        root=root,
    )
    _run(
        "Validate current TTM numerators",
        [
            python,
            "scripts/validate_v2_ttm_numerators.py",
            "--as-of",
            args.as_of.isoformat(),
        ],
        root=root,
    )

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    manifest = _read_json(v2["manifest"])
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("Weekly V2 SEC research did not complete")
    if any(bool(v) for v in manifest.get("execution_capabilities", {}).values()):
        raise SystemExit("V2 manifest enables an execution capability")

    reconstruction = _read_json(ttm["enriched_summary"])
    if reconstruction.get("winner_source") != "v2_current_ttm_duration_overlay":
        raise SystemExit("Weekly TTM reconstruction did not use current overlay")
    if int(reconstruction.get("pit_violations", 1)) != 0:
        raise SystemExit("Weekly TTM reconstruction has PIT violations")

    validation = _read_json(ttm["ttm_validation_summary"])
    if validation.get("status") != "CURRENT_TTM_NUMERATOR_VALIDATION_COMPLETE":
        raise SystemExit("Weekly TTM numerator validation did not complete")
    if int(validation.get("pit_violations", 1)) != 0:
        raise SystemExit("Weekly TTM numerators have PIT violations")
    if validation.get("income_quarter_policy") != "ytd_preferred":
        raise SystemExit("Weekly TTM numerators are not YTD-preferred")

    direct_inputs = [
        v1_decision_path,
        market_path,
        discovery,
        v2["manifest"],
        v2["input_fingerprints"],
        v2["scoring_panel"],
        v2["current_snapshot"],
        ttm["current_duration_summary"],
        ttm["enriched_summary"],
        ttm["ttm_validation_summary"],
        ttm["ttm_current_numerators"],
        ttm["ttm_latest_by_concept"],
    ]
    input_fingerprints = fingerprint_files(root=root, paths=direct_inputs)
    provenance_after = git_provenance(root)
    if (
        provenance_after["commit"] != provenance["commit"]
        or not provenance_after["tracked_worktree_clean"]
    ):
        raise SystemExit("Code provenance changed during V2 shadow run")

    print()
    print("=== Score frozen V2 TTM challenger ===", flush=True)
    scoring_panel = pd.read_csv(v2["scoring_panel"], low_memory=False)
    current_ttm = pd.read_csv(ttm["ttm_current_numerators"], low_memory=False)
    latest_ttm = pd.read_csv(ttm["ttm_latest_by_concept"], low_memory=False)
    scored_panel, v2_current = build_current_ttm_challenger(
        scoring_panel,
        current_ttm,
        latest_ttm,
        as_of=pd.Timestamp(args.as_of),
    )

    comparison = build_top10_comparison(
        v1_decision=v1_decision,
        v2_current=v2_current,
    )
    overlap = int(comparison["in_both"].sum())
    entered = sorted(
        comparison.loc[comparison["change"].eq("entered_v2"), "ticker"]
        .astype(str)
        .tolist()
    )
    exited = sorted(
        comparison.loc[comparison["change"].eq("exited_v2"), "ticker"]
        .astype(str)
        .tolist()
    )

    decision = build_v2_decision_payload(
        as_of=args.as_of.isoformat(),
        v1_decision_hash=v1_hash,
        v2_current=v2_current,
        input_bundle_sha256=str(input_fingerprints["sha256"]),
    )

    score_column = f"{TTM_CHALLENGER.model_id}_score"
    current_rows = len(v2_current)
    ttm_valuation_eligible = int(
        v2_current["ttm_valuation_eligible"].fillna(False).astype(bool).sum()
    )
    v2_top_conviction = int(
        v2_current["v2_top_conviction_eligible"].fillna(False).astype(bool).sum()
    )

    shadow["shadow_dir"].mkdir(parents=True, exist_ok=False)
    scored_panel.to_csv(shadow["v2_scored_panel"], index=False)
    v2_current.to_csv(shadow["v2_current_scores"], index=False)
    comparison.to_csv(shadow["comparison"], index=False)
    shadow["decision"].write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shadow["input_fingerprints"].write_text(
        json.dumps({
            "schema_version": 1,
            "as_of": args.as_of.isoformat(),
            "files": input_fingerprints,
            "code": provenance_after,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "status": "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "model_id": TTM_CHALLENGER.model_id,
        "v1_decision_hash": v1_hash,
        "v2_decision_hash": decision["decision_hash"],
        "current_universe_rows": current_rows,
        "ttm_valuation_eligible": ttm_valuation_eligible,
        "v2_top_conviction_eligible": v2_top_conviction,
        "v1_v2_top10_overlap": overlap,
        "v2_entered_top10": entered,
        "v2_exited_top10": exited,
        "pit_violations": 0,
        "input_bundle_sha256": input_fingerprints["sha256"],
        "code_commit": provenance_after["commit"],
        "tracked_worktree_clean": provenance_after["tracked_worktree_clean"],
        "shadow_week_valid": True,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }
    shadow["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    shadow["ledger_dir"].mkdir(parents=True, exist_ok=True)
    if shadow["ledger"].exists():
        ledger = pd.read_csv(shadow["ledger"], low_memory=False)
    else:
        ledger = pd.DataFrame()

    ledger_row = {
        "as_of": args.as_of.isoformat(),
        "model_id": TTM_CHALLENGER.model_id,
        "v1_decision_hash": v1_hash,
        "v2_decision_hash": decision["decision_hash"],
        "current_universe_rows": current_rows,
        "ttm_valuation_eligible": ttm_valuation_eligible,
        "v2_top_conviction_eligible": v2_top_conviction,
        "top10_overlap": overlap,
        "entered_v2": "|".join(entered),
        "exited_v2": "|".join(exited),
        "pit_violations": 0,
        "input_bundle_sha256": input_fingerprints["sha256"],
        "code_commit": provenance_after["commit"],
        "valid": True,
    }
    ledger = append_shadow_ledger(ledger, ledger_row)
    ledger.to_csv(shadow["ledger"], index=False)

    valid_count = int(ledger["valid"].fillna(False).astype(bool).sum())
    ledger_summary = {
        "schema_version": 1,
        "model_id": TTM_CHALLENGER.model_id,
        "valid_shadow_weeks": valid_count,
        "required_shadow_weeks": TTM_PROMOTION_THRESHOLDS.minimum_shadow_weeks,
        "shadow_requirement_satisfied": (
            valid_count >= TTM_PROMOTION_THRESHOLDS.minimum_shadow_weeks
        ),
        "first_valid_week": (
            str(ledger.loc[ledger["valid"].fillna(False).astype(bool), "as_of"].min())
            if valid_count
            else None
        ),
        "latest_valid_week": (
            str(ledger.loc[ledger["valid"].fillna(False).astype(bool), "as_of"].max())
            if valid_count
            else None
        ),
        "live_promotion_authorized": False,
    }
    shadow["ledger_summary"].write_text(
        json.dumps(ledger_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 72)
    print("V2 TTM SHADOW OBSERVATION COMPLETE")
    print(f"As of:                      {args.as_of.isoformat()}")
    print(f"Model:                      {TTM_CHALLENGER.model_id}")
    print(f"V1 decision hash:           {v1_hash}")
    print(f"V2 decision hash:           {decision['decision_hash']}")
    print(f"V1/V2 Top-10 overlap:       {overlap}/10")
    print(
        "V2 entered / exited:       "
        f"{','.join(entered) if entered else '-'} / "
        f"{','.join(exited) if exited else '-'}"
    )
    print(
        f"TTM valuation eligible:     "
        f"{ttm_valuation_eligible:,}/{current_rows:,}"
    )
    print(
        f"V2 Top-Conviction eligible: "
        f"{v2_top_conviction:,}/{current_rows:,}"
    )
    print("PIT violations:             0")
    print(
        f"Valid shadow weeks:         {valid_count}/"
        f"{TTM_PROMOTION_THRESHOLDS.minimum_shadow_weeks}"
    )
    print(f"Weekly summary:             {shadow['summary']}")
    print(f"Shadow ledger:              {shadow['ledger']}")
    print()
    print("NO ORDER INTENTS, ORDER REVIEW, OR ORDER PLACEMENT WERE RUN.")
    print("V1 REMAINS THE LIVE CHAMPION.")
    print("=" * 72)


if __name__ == "__main__":
    main()
