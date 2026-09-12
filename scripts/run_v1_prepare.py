from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable
import uuid

import pandas as pd


SAFE_TESTS = (
    "tests/test_post_fill.py",
    "tests/test_submission_receipt.py",
    "tests/test_run_log.py",
    "tests/test_pre_submit.py",
    "tests/test_order_intent.py",
    "tests/test_execution_gate.py",
    "tests/test_robinhood_shadow_state.py",
    "tests/test_robinhood_market_snapshot.py",
    "tests/test_robinhood_gateway.py",
    "tests/test_shadow_decision.py",
    "tests/test_weekly_workflow.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the safe automated portion of long_growth_v1: refresh current SEC evidence, "
            "pull Robinhood account/market data directly, rebuild the frozen model, and stop "
            "after broker-ready order intents. This script never places orders."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--pitindex-data", type=Path, default=None)
    parser.add_argument("--symbols-file", type=Path, default=None)
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--market-max-age-minutes", type=float, default=1440.0)
    parser.add_argument("--min-valid-price-fraction", type=float, default=0.95)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--skip-sec-refresh",
        action="store_true",
        help="Use the existing SEC shadow winner cache without contacting SEC.",
    )
    parser.add_argument(
        "--reuse-broker-inputs",
        action="store_true",
        help="Reuse broker_snapshot_pre.json and robinhood_market_snapshot.json in the dated run dir.",
    )
    return parser.parse_args()


def run_command(label: str, args: Iterable[str], *, cwd: Path) -> None:
    command = [str(value) for value in args]
    print()
    print(f"=== {label} ===", flush=True)
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mark_stage(state: dict, stage: str, status: str) -> None:
    state.setdefault("stage_status", {})[stage] = status
    label = stage if status == "completed" else f"{stage}_{status}"
    state.setdefault("completed_stages", []).append(label)


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    repo = args.repo_root.resolve()
    pitindex = (
        args.pitindex_data.resolve()
        if args.pitindex_data is not None
        else (repo.parent / "pitindex" / "pitindex" / "data").resolve()
    )
    symbols = (
        args.symbols_file.resolve()
        if args.symbols_file is not None
        else pitindex / "sp500_current.csv"
    )
    run_dir = repo / "reports" / "shadow" / args.as_of.isoformat()
    return {
        "repo": repo,
        "pitindex": pitindex,
        "symbols": symbols,
        "run_dir": run_dir,
        "run_log": run_dir / "run_log.jsonl",
        "broker_pre": run_dir / "broker_snapshot_pre.json",
        "market_raw": run_dir / "robinhood_market_snapshot.json",
        "market_normalized": run_dir / "robinhood_market_snapshot_normalized.csv",
        "market_summary": run_dir / "robinhood_market_snapshot_summary.csv",
        "portfolio_state": run_dir / "portfolio_state.csv",
        "non_final_orders": run_dir / "non_final_orders.csv",
        "historical_panel": repo / "reports" / "weekly_research_panel_2015_2025.csv",
        "sec_discovery": repo / "reports" / "sec_current_filing_discovery.csv",
        "sec_candidates": repo / "reports" / "sec_current_candidate_facts.csv",
        "sec_shadow": repo / "data" / "cache" / "sec" / "shadow" / "sec_winner_facts_shadow.csv",
        "scoring_panel": repo / "reports" / "current_shadow_scoring_panel.csv",
        "current_snapshot": repo / "reports" / "current_shadow_snapshot.csv",
        "raw_factors": repo / "reports" / "current_shadow_raw_factors.csv",
        "normalized_factors": repo / "reports" / "current_shadow_normalized_factors.csv",
        "family_scores": repo / "reports" / "current_shadow_family_scores.csv",
        "long_growth": repo / "reports" / "current_shadow_long_growth_v1.csv",
        "validation_dir": repo / "reports" / "current_shadow_long_growth_validation",
        "decision": run_dir / "shadow_decision.json",
        "execution_gate": run_dir / "execution_gate.json",
        "order_intents": run_dir / "order_intents.json",
        "state": run_dir / "workflow_state.json",
    }


def ensure_inputs(paths: dict[str, Path]) -> None:
    required = ("pitindex", "symbols", "historical_panel")
    missing = [f"{key}: {paths[key]}" for key in required if not paths[key].exists()]
    if missing:
        raise SystemExit("Missing required input(s):\n  " + "\n  ".join(missing))


def refresh_sec(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    repo = paths["repo"]
    python = sys.executable
    if not os.environ.get("SEC_USER_AGENT", "").strip():
        raise SystemExit(
            "SEC_USER_AGENT is required for automated SEC refresh. "
            "Set it or rerun with --skip-sec-refresh."
        )

    run_command(
        "SEC current filing discovery",
        [python, "scripts/update_sec_current.py", "--pitindex-data", paths["pitindex"], "--as-of", args.as_of.isoformat(), "--output", paths["sec_discovery"]],
        cwd=repo,
    )

    discovery = pd.read_csv(paths["sec_discovery"], low_memory=False)
    statuses = discovery.get("status", pd.Series(dtype="string")).astype(str)
    partial = int(statuses.eq("new_filing_partial").sum())
    errors = int(statuses.eq("submissions_error").sum())
    cached = int(statuses.eq("new_filing_cached").sum())

    if partial or errors:
        raise SystemExit(
            "SEC refresh failed closed: "
            f"new_filing_partial={partial}, submissions_error={errors}. "
            f"Inspect {paths['sec_discovery']}."
        )

    if cached == 0:
        if not paths["sec_shadow"].exists():
            raise SystemExit(
                "No new SEC filings were found and the existing SEC shadow cache is missing: "
                f"{paths['sec_shadow']}"
            )
        print("No new supported SEC filings; keeping existing SEC shadow cache.", flush=True)
        return

    run_command(
        "SEC current candidate build",
        [python, "scripts/build_sec_current_candidates.py", "--discovery", paths["sec_discovery"], "--output", paths["sec_candidates"]],
        cwd=repo,
    )
    run_command(
        "SEC shadow merge",
        [python, "scripts/build_sec_shadow_merge.py", "--current-candidates", paths["sec_candidates"], "--as-of", args.as_of.isoformat(), "--output", paths["sec_shadow"]],
        cwd=repo,
    )


def validate_market_summary(paths: dict[str, Path], minimum_fraction: float) -> None:
    summary = pd.read_csv(paths["market_summary"], low_memory=False)
    if len(summary) != 1:
        raise SystemExit(f"Unexpected market summary shape: {paths['market_summary']}")
    row = summary.iloc[0]
    exact = int(row["exact_symbol_matches"])
    valid = int(row["valid_prices"])
    fraction = valid / exact if exact else 0.0
    print(f"Market validity:            {valid:,}/{exact:,} ({fraction:.2%})", flush=True)
    if exact <= 0 or fraction < minimum_fraction:
        raise SystemExit(
            "Market data failed closed: "
            f"valid/exact={valid}/{exact} ({fraction:.2%}) < {minimum_fraction:.2%}."
        )


def main() -> None:
    args = parse_args()
    if args.weekly_contribution > 10.0 + 1e-9:
        raise SystemExit("long_growth_v1 weekly contribution may not exceed $10.")
    if not 0 < args.min_valid_price_fraction <= 1:
        raise SystemExit("--min-valid-price-fraction must be in (0, 1].")

    paths = resolve_paths(args)
    ensure_inputs(paths)
    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    repo = paths["repo"]
    python = sys.executable

    run_id = f"long_growth_v1-{args.as_of.isoformat()}"
    attempt_id = uuid.uuid4().hex
    os.environ["FINANCE_RUN_ID"] = run_id
    os.environ["FINANCE_ATTEMPT_ID"] = attempt_id

    state = {
        "as_of": args.as_of.isoformat(),
        "model": "long_growth_v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "status": "RUNNING",
        "safe_only": True,
        "order_submission_enabled": False,
        "run_dir": str(paths["run_dir"]),
        "completed_stages": [],
        "stage_status": {},
    }
    write_json(paths["state"], state)

    try:
        if not args.skip_tests:
            run_command("Safety tests", [python, "-m", "pytest", *SAFE_TESTS], cwd=repo)
            mark_stage(state, "tests", "completed")
        else:
            mark_stage(state, "tests", "skipped")
        write_json(paths["state"], state)

        if not args.skip_sec_refresh:
            refresh_sec(args, paths)
            mark_stage(state, "sec_refresh", "completed")
        else:
            if not paths["sec_shadow"].exists():
                raise SystemExit(f"SEC shadow cache not found: {paths['sec_shadow']}")
            mark_stage(state, "sec_refresh", "reused")
        write_json(paths["state"], state)

        if not args.reuse_broker_inputs:
            run_command(
                "Robinhood direct live input export",
                [python, "scripts/export_robinhood_live_inputs.py", "--symbols-file", paths["symbols"], "--run-dir", paths["run_dir"], "--check-universe-tradability"],
                cwd=repo,
            )
            mark_stage(state, "robinhood_export", "completed")
        else:
            mark_stage(state, "robinhood_export", "reused")
        for key in ("broker_pre", "market_raw"):
            if not paths[key].exists():
                raise SystemExit(f"Missing Robinhood input after export: {paths[key]}")
        write_json(paths["state"], state)

        run_command(
            "Normalize broker state",
            [python, "scripts/normalize_robinhood_shadow_state.py", "--input", paths["broker_pre"], "--portfolio-output", paths["portfolio_state"], "--orders-output", paths["non_final_orders"], "--run-log", paths["run_log"]],
            cwd=repo,
        )
        mark_stage(state, "normalize_broker_state", "completed")
        write_json(paths["state"], state)

        market_payload = read_json(paths["market_raw"])
        metadata = market_payload.get("export_metadata", {})
        market_as_of = metadata.get("capture_completed_at") or metadata.get("export_created_at") or metadata.get("capture_started_at")
        if not market_as_of:
            raise SystemExit("Robinhood market snapshot is missing a capture timestamp.")

        run_command(
            "Normalize market snapshot",
            [python, "scripts/normalize_robinhood_market_snapshot.py", "--input", paths["market_raw"], "--as-of", market_as_of, "--max-price-age-minutes", str(args.market_max_age_minutes), "--output", paths["market_normalized"], "--summary-output", paths["market_summary"]],
            cwd=repo,
        )
        validate_market_summary(paths, args.min_valid_price_fraction)
        mark_stage(state, "normalize_market_snapshot", "completed")
        write_json(paths["state"], state)

        run_command(
            "Build current shadow scoring panel",
            [python, "scripts/build_current_shadow_panel.py", "--pitindex-data", paths["pitindex"], "--historical-panel", paths["historical_panel"], "--shadow-winners", paths["sec_shadow"], "--market-snapshot", paths["market_normalized"], "--as-of", args.as_of.isoformat(), "--output", paths["scoring_panel"], "--current-output", paths["current_snapshot"]],
            cwd=repo,
        )

        factor_commands = (
            ("Build raw factors", [python, "scripts/build_raw_factors.py", "--panel", paths["scoring_panel"], "--output", paths["raw_factors"]]),
            ("Build normalized factors", [python, "scripts/build_normalized_factors.py", "--factors", paths["raw_factors"], "--output", paths["normalized_factors"]]),
            ("Build family scores", [python, "scripts/build_family_scores.py", "--normalized", paths["normalized_factors"], "--output", paths["family_scores"]]),
            ("Build long_growth_v1", [python, "scripts/build_long_growth_v1.py", "--family-scores", paths["family_scores"], "--output", paths["long_growth"]]),
            ("Audit long_growth_v1", [python, "scripts/audit_long_growth_v1.py", "--composite", paths["long_growth"], "--output-dir", paths["validation_dir"]]),
        )
        for label, command in factor_commands:
            run_command(label, command, cwd=repo)
        mark_stage(state, "current_model_refresh", "completed")
        write_json(paths["state"], state)

        run_command(
            "Build shadow decision",
            [python, "scripts/build_shadow_decision.py", "--long-growth", paths["long_growth"], "--portfolio-state", paths["portfolio_state"], "--broker-state", paths["broker_pre"], "--as-of", args.as_of.isoformat(), "--weekly-contribution", str(args.weekly_contribution), "--output-dir", paths["run_dir"], "--run-log", paths["run_log"]],
            cwd=repo,
        )
        mark_stage(state, "shadow_decision", "completed")
        write_json(paths["state"], state)

        run_command(
            "Evaluate execution gate",
            [python, "scripts/evaluate_execution_gate.py", "--decision", paths["decision"], "--broker-state", paths["broker_pre"], "--output", paths["execution_gate"], "--run-log", paths["run_log"]],
            cwd=repo,
        )
        gate = read_json(paths["execution_gate"])
        if not gate.get("ready"):
            raise SystemExit(
                "Execution gate failed closed: "
                + ", ".join(str(reason) for reason in gate.get("reasons", []))
            )
        mark_stage(state, "execution_gate", "completed")
        write_json(paths["state"], state)

        run_command(
            "Build order intents",
            [python, "scripts/build_order_intents.py", "--decision", paths["decision"], "--execution-gate", paths["execution_gate"], "--output-dir", paths["run_dir"], "--run-log", paths["run_log"]],
            cwd=repo,
        )
        mark_stage(state, "order_intents", "completed")
        state["status"] = "READY_FOR_PRESUBMIT_REFRESH"
        state["artifacts"] = {
            "broker_snapshot_pre": str(paths["broker_pre"]),
            "market_snapshot": str(paths["market_raw"]),
            "market_normalized": str(paths["market_normalized"]),
            "scoring_panel": str(paths["scoring_panel"]),
            "long_growth_v1": str(paths["long_growth"]),
            "shadow_decision": str(paths["decision"]),
            "execution_gate": str(paths["execution_gate"]),
            "order_intents": str(paths["order_intents"]),
        }
        write_json(paths["state"], state)

        decision = read_json(paths["decision"])
        intents = read_json(paths["order_intents"])
        print()
        print("=" * 72)
        print("V1 SAFE PREPARATION COMPLETE")
        print(f"Status:                     {state['status']}")
        print(f"Run ID:                     {run_id}")
        print(f"Attempt ID:                 {attempt_id}")
        print(f"Decision hash:              {decision.get('decision_hash')}")
        print(f"Planned investment:         ${float(decision.get('planned_investment', 0.0)):.2f}")
        count = len(intents.get("orders") or intents.get("intents") or [])
        print(f"Order intents:              {count}")
        print(f"Workflow state:             {paths['state']}")
        print()
        print("NO ORDERS WERE PLACED.")
        print("Next stage is a fresh pre-submit broker snapshot and pre-submit gate.")
        print("=" * 72)

    except BaseException as exc:
        state["status"] = "FAILED"
        state["error"] = str(exc)
        write_json(paths["state"], state)
        raise


if __name__ == "__main__":
    main()
