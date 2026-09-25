from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence


DEFAULT_SHADOW_REGISTRY = Path("config/live_shadow_modes.json")

PRESUBMIT_RESUME_STATUSES = {
    "AWAITING_APPROVAL",
    "PRESUBMIT_RUNNING",
    "PRESUBMIT_BLOCKED",
    "PRESUBMIT_FAILED",
}

POSTFILL_RESUME_STATUSES = {
    "SUBMITTED_RECONCILED",
    "POSTFILL_RUNNING",
    "POSTFILL_PENDING",
    "POSTFILL_RECONCILIATION_REQUIRED",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run or resume the long_growth_v1 live workflow as one interactive "
            "pipeline. Existing stage scripts remain authoritative for all "
            "safety gates and broker actions."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--shadow-registry",
        type=Path,
        default=DEFAULT_SHADOW_REGISTRY,
        help=(
            "Registry of research-only shadow modes. Relative paths resolve "
            "under --repo-root."
        ),
    )
    parser.add_argument(
        "--sec-user-agent",
        default=None,
        help=(
            "Optional SEC_USER_AGENT value for this run. If omitted, the "
            "existing SEC_USER_AGENT environment variable is used."
        ),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def workflow_status(run_dir: Path) -> str | None:
    state_path = run_dir / "workflow_state.json"
    if not state_path.exists():
        return None
    try:
        return str(read_json(state_path).get("status") or "") or None
    except (OSError, json.JSONDecodeError):
        return "INVALID_STATE"


def workflow_action(status: str | None, *, receipt_exists: bool = False) -> str:
    if status is None or status in {"FAILED", "RUNNING"}:
        return "prepare"
    if status == "READY_FOR_PRESUBMIT_REFRESH" or status in PRESUBMIT_RESUME_STATUSES:
        return "continue"
    if status == "SUBMISSION_REQUIRES_RECONCILIATION":
        return "recover_submission"
    if status == "SUBMISSION_RUNNING":
        return "recover_submission" if receipt_exists else "stop_ambiguous_submission"
    if status in POSTFILL_RESUME_STATUSES:
        return "postfill"
    if status == "COMPLETE":
        return "complete"
    return "stop_unknown"


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_stage(
    label: str,
    command: Sequence[str | Path],
    *,
    cwd: Path,
    required: bool = True,
) -> bool:
    print()
    print("=" * 72, flush=True)
    print(label, flush=True)
    print("=" * 72, flush=True)
    try:
        subprocess.run([str(value) for value in command], cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        if required:
            raise
        print(
            f"WARNING: {label} failed with exit code {exc.returncode}. "
            "This research-only stage does not block an otherwise valid V1 live run.",
            flush=True,
        )
        return False


def _resolve_registry_path(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def load_shadow_registry(repo: Path, registry_path: Path) -> list[dict]:
    path = _resolve_registry_path(repo, registry_path)
    if not path.exists():
        raise SystemExit(f"Shadow registry not found: {path}")
    payload = read_json(path)
    if int(payload.get("schema_version") or 0) != 1:
        raise SystemExit("Unsupported live shadow registry schema_version")

    raw_modes = payload.get("modes")
    if not isinstance(raw_modes, list):
        raise SystemExit("Shadow registry modes must be a list")

    modes: dict[str, dict] = {}
    for raw in raw_modes:
        if not isinstance(raw, dict):
            raise SystemExit("Every shadow registry mode must be an object")
        mode = dict(raw)
        mode_id = str(mode.get("id") or "").strip()
        required = ("label", "runner", "summary", "expected_status")
        missing = [field for field in required if not str(mode.get(field) or "").strip()]
        if not mode_id or missing:
            raise SystemExit(
                "Invalid shadow registry mode: "
                f"id={mode_id!r}, missing={','.join(missing)}"
            )
        if mode_id in modes:
            raise SystemExit(f"Duplicate shadow mode id: {mode_id}")
        depends_on = mode.get("depends_on") or []
        if not isinstance(depends_on, list):
            raise SystemExit(f"Shadow mode {mode_id} depends_on must be a list")
        mode["depends_on"] = [str(value) for value in depends_on]
        modes[mode_id] = mode

    for mode_id, mode in modes.items():
        unknown = [dep for dep in mode["depends_on"] if dep not in modes]
        if unknown:
            raise SystemExit(
                f"Shadow mode {mode_id} has unknown dependencies: {', '.join(unknown)}"
            )

    ordered: list[dict] = []
    remaining = dict(modes)
    resolved: set[str] = set()
    while remaining:
        ready = [
            mode_id
            for mode_id, mode in remaining.items()
            if set(mode["depends_on"]).issubset(resolved)
        ]
        if not ready:
            raise SystemExit("Shadow registry contains a dependency cycle")
        for mode_id in ready:
            ordered.append(remaining.pop(mode_id))
            resolved.add(mode_id)
    return ordered


def _mode_path(repo: Path, template: str, as_of: date) -> Path:
    rendered = template.format(as_of=as_of.isoformat())
    path = Path(rendered)
    return path if path.is_absolute() else repo / path


def shadow_observation_state(
    mode: dict,
    *,
    repo: Path,
    as_of: date,
    expected_v1_decision_hash: str,
) -> str:
    summary_path = _mode_path(repo, str(mode["summary"]), as_of)
    if not summary_path.exists():
        return "missing"
    try:
        summary = read_json(summary_path)
    except (OSError, json.JSONDecodeError):
        return "invalid"

    if str(summary.get("status") or "") != str(mode["expected_status"]):
        return "invalid"
    if mode.get("require_zero_pit_violations", True):
        try:
            if int(summary.get("pit_violations", 0)) != 0:
                return "invalid"
        except (TypeError, ValueError):
            return "invalid"
    if mode.get("require_shadow_week_valid", False):
        if not bool(summary.get("shadow_week_valid", False)):
            return "invalid"
    if mode.get("require_v1_decision_hash", True):
        if str(summary.get("v1_decision_hash") or "") != expected_v1_decision_hash:
            return "hash_mismatch"
    return "complete"


def archive_partial_shadow_work(
    mode: dict,
    *,
    repo: Path,
    as_of: date,
) -> Path | None:
    template = str(mode.get("work_dir") or "").strip()
    if not template:
        return None
    work_dir = _mode_path(repo, template, as_of)
    if not work_dir.exists():
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archived = work_dir.with_name(f"{work_dir.name}.failed-{stamp}")
    archived.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(work_dir), str(archived))
    return archived


def run_shadow_modes(
    modes: list[dict],
    *,
    repo: Path,
    as_of: date,
    python: str,
    v1_decision_hash: str,
) -> tuple[dict[str, bool], list[str]]:
    results: dict[str, bool] = {}
    warnings: list[str] = []

    for index, mode in enumerate(modes, start=1):
        mode_id = str(mode["id"])
        label = str(mode["label"])
        dependencies = [str(value) for value in mode.get("depends_on") or []]
        failed_dependencies = [dep for dep in dependencies if not results.get(dep, False)]
        if failed_dependencies:
            print()
            print("=" * 72)
            print(f"SHADOW {index}/{len(modes)} - {label}")
            print("=" * 72)
            print(
                "Skipping because prerequisite shadow mode(s) did not complete: "
                + ", ".join(failed_dependencies)
            )
            results[mode_id] = False
            warnings.append(
                f"{label} skipped because prerequisite shadow mode(s) failed: "
                + ", ".join(failed_dependencies)
            )
            continue

        state = shadow_observation_state(
            mode,
            repo=repo,
            as_of=as_of,
            expected_v1_decision_hash=v1_decision_hash,
        )
        if state == "complete":
            print()
            print("=" * 72)
            print(f"SHADOW {index}/{len(modes)} - {label}")
            print("=" * 72)
            print("Reusing completed valid shadow observation for this V1 decision.")
            results[mode_id] = True
            continue

        if state == "hash_mismatch":
            print()
            print("=" * 72)
            print(f"SHADOW {index}/{len(modes)} - {label}")
            print("=" * 72)
            print(
                "Existing completed observation belongs to a different V1 "
                "decision hash; preserving it as immutable and not counting it "
                "for this run."
            )
            results[mode_id] = False
            warnings.append(
                f"{label} has a completed observation for a different V1 decision hash."
            )
            continue

        archived = archive_partial_shadow_work(
            mode,
            repo=repo,
            as_of=as_of,
        )
        if archived is not None:
            print(
                f"Preserved incomplete prior research attempt at: {archived}",
                flush=True,
            )

        runner = _mode_path(repo, str(mode["runner"]), as_of)
        ok = run_stage(
            f"SHADOW {index}/{len(modes)} - {label}",
            [
                python,
                runner,
                "--as-of",
                as_of.isoformat(),
                "--repo-root",
                repo,
            ],
            cwd=repo,
            required=not bool(mode.get("non_blocking", True)),
        )
        if ok:
            final_state = shadow_observation_state(
                mode,
                repo=repo,
                as_of=as_of,
                expected_v1_decision_hash=v1_decision_hash,
            )
            ok = final_state == "complete"
            if not ok:
                print(
                    f"WARNING: {label} returned success but completion artifact "
                    f"state is {final_state}.",
                    flush=True,
                )

        results[mode_id] = ok
        if not ok:
            warnings.append(
                f"{label} did not produce a valid observation and does not count for this week."
            )

    return results, warnings


def approval_snapshot(run_dir: Path) -> dict:
    intents = read_json(run_dir / "order_intents.json")
    gate = read_json(run_dir / "pre_submit_gate.json")
    reviews = read_json(run_dir / "order_reviews.json")

    rows = intents.get("intents") or []
    validation = reviews.get("validation") or {}
    snapshot_created_at = str(gate.get("snapshot_created_at") or "")
    current_age_minutes = None
    if snapshot_created_at:
        current_age_minutes = (
            datetime.now(timezone.utc) - parse_utc(snapshot_created_at)
        ).total_seconds() / 60.0

    return {
        "decision_hash": str(intents.get("decision_hash") or ""),
        "order_count": int(intents.get("order_count") or len(rows)),
        "total_dollars": float(intents.get("total_dollars") or 0.0),
        "orders": [
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "amount_dollars": float(row.get("amount_dollars") or 0.0),
                "order_type": str(row.get("order_type") or ""),
                "market_hours": str(row.get("market_hours") or ""),
            }
            for row in rows
        ],
        "snapshot_age_minutes": current_age_minutes,
        "buying_power": float(gate.get("buying_power") or 0.0),
        "tradable_count": int(gate.get("tradable_count") or 0),
        "gate_order_count": int(gate.get("order_count") or len(rows)),
        "reviews_clean": int(validation.get("clean_count") or 0),
        "reviews_count": int(validation.get("reviewed_count") or len(rows)),
    }


def print_approval_checkpoint(
    *,
    as_of: date,
    snapshot: dict,
    warnings: Sequence[str],
) -> None:
    print()
    print("=" * 72)
    print("LIVE ORDER APPROVAL REQUIRED")
    print("=" * 72)
    print(f"Trading date:                {as_of.isoformat()}")
    print(f"Decision hash:               {snapshot['decision_hash']}")
    print(f"Order count:                 {snapshot['order_count']}")
    print(f"Total dollars:               ${snapshot['total_dollars']:.2f}")
    age = snapshot.get("snapshot_age_minutes")
    print(
        "Pre-submit snapshot age:    "
        + (f"{age:.2f} minutes" if age is not None else "UNKNOWN")
    )
    print(f"Buying power:                ${snapshot['buying_power']:,.2f}")
    print(
        f"Tradable intents:            {snapshot['tradable_count']}/"
        f"{snapshot['gate_order_count']}"
    )
    print(
        f"Robinhood reviews clean:     {snapshot['reviews_clean']}/"
        f"{snapshot['reviews_count']}"
    )
    print()
    print("ORDERS TO BE SUBMITTED")
    for row in snapshot["orders"]:
        details = ", ".join(
            part
            for part in (row["order_type"], row["market_hours"])
            if part
        )
        suffix = f" ({details})" if details else ""
        print(f"  {row['ticker']:<8} ${row['amount_dollars']:>5.2f}{suffix}")

    if warnings:
        print()
        print("NON-BLOCKING RESEARCH WARNINGS")
        for warning in warnings:
            print(f"  - {warning}")

    print()
    print("Entering y/Y will invoke the existing approved submission path.")
    print("That path will re-check package freshness, the NYSE session, and the")
    print("fresh SPY benchmark gate before any order placement call.")
    print("Any other response means NO APPROVAL and no live orders will be placed.")
    print("=" * 72)


def prompt_for_approval() -> bool:
    try:
        answer = input("Approve these live orders? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "y"


def print_resume_banner(status: str | None, action: str) -> None:
    print()
    print("=" * 72)
    print("LIVE PIPELINE RESUME CHECK")
    print("=" * 72)
    print(f"Saved workflow status:       {status or 'NONE'}")
    print(f"Selected action:             {action}")
    print("=" * 72)


def run_postfill_resume(
    *,
    repo: Path,
    as_of: date,
    python: str,
) -> str:
    run_stage(
        "RESUME - READ-ONLY POST-FILL VERIFICATION",
        [
            python,
            "scripts/run_v1_postfill.py",
            "--as-of",
            as_of.isoformat(),
            "--repo-root",
            repo,
        ],
        cwd=repo,
    )
    return workflow_status(repo / "reports" / "shadow" / as_of.isoformat()) or "UNKNOWN"


def main() -> None:
    args = parse_args()
    repo = args.repo_root.resolve()
    run_dir = repo / "reports" / "shadow" / args.as_of.isoformat()
    state_path = run_dir / "workflow_state.json"
    receipt_path = run_dir / "broker_submission_receipt.json"
    python = sys.executable

    if args.sec_user_agent:
        os.environ["SEC_USER_AGENT"] = args.sec_user_agent

    status = workflow_status(run_dir)
    action = workflow_action(status, receipt_exists=receipt_path.exists())
    print_resume_banner(status, action)

    if action == "complete":
        print("This live run is already COMPLETE. No broker or order action was taken.")
        return

    if action == "stop_ambiguous_submission":
        raise SystemExit(
            "STOP: workflow is SUBMISSION_RUNNING but no saved broker receipt exists. "
            "Order placement may have occurred. Do not rerun approved submission."
        )

    if action == "stop_unknown":
        raise SystemExit(
            f"STOP: unsupported workflow state {status!r}. Inspect {state_path} "
            "before taking any broker/order action."
        )

    if action == "recover_submission":
        run_stage(
            "RESUME - READ-ONLY SUBMISSION RECONCILIATION",
            [
                python,
                "scripts/recover_v1_submission.py",
                "--as-of",
                args.as_of.isoformat(),
                "--repo-root",
                repo,
            ],
            cwd=repo,
        )
        recovered_status = workflow_status(run_dir)
        if recovered_status == "SUBMITTED_RECONCILED":
            final_status = run_postfill_resume(
                repo=repo,
                as_of=args.as_of,
                python=python,
            )
            print(f"Resume result:               {final_status}")
        else:
            print(
                "Submission is still not fully reconciled. No new orders were "
                "placed; inspect the saved recovery artifacts."
            )
        return

    if action == "postfill":
        final_status = run_postfill_resume(
            repo=repo,
            as_of=args.as_of,
            python=python,
        )
        print(f"Resume result:               {final_status}")
        return

    if action == "prepare":
        if not os.environ.get("SEC_USER_AGENT", "").strip():
            raise SystemExit(
                "SEC_USER_AGENT is required for a fresh live preparation. Set it "
                "in the environment or pass --sec-user-agent."
            )
        run_stage(
            "V1 PREPARE",
            [
                python,
                "scripts/run_v1_prepare.py",
                "--as-of",
                args.as_of.isoformat(),
                "--repo-root",
                repo,
            ],
            cwd=repo,
        )
        status = workflow_status(run_dir)
        if status != "READY_FOR_PRESUBMIT_REFRESH":
            raise SystemExit(
                "V1 preparation did not reach READY_FOR_PRESUBMIT_REFRESH: "
                f"status={status!r}"
            )
    else:
        print("Reusing the existing frozen V1 decision and order intents.")

    decision_path = run_dir / "shadow_decision.json"
    if not decision_path.exists():
        raise SystemExit(f"Missing frozen V1 decision: {decision_path}")
    v1_decision = read_json(decision_path)
    v1_decision_hash = str(v1_decision.get("decision_hash") or "")
    if not v1_decision_hash:
        raise SystemExit("Prepared V1 decision is missing decision_hash.")

    if not os.environ.get("SEC_USER_AGENT", "").strip():
        raise SystemExit(
            "SEC_USER_AGENT is required while running registered research shadow modes."
        )

    modes = load_shadow_registry(repo, args.shadow_registry)
    _, warnings = run_shadow_modes(
        modes,
        repo=repo,
        as_of=args.as_of,
        python=python,
        v1_decision_hash=v1_decision_hash,
    )

    status = workflow_status(run_dir)
    presubmit_command: list[str | Path] = [
        python,
        "scripts/run_v1_presubmit.py",
        "--as-of",
        args.as_of.isoformat(),
        "--repo-root",
        repo,
    ]
    if status in PRESUBMIT_RESUME_STATUSES:
        presubmit_command.append("--resume-existing")
    elif status != "READY_FOR_PRESUBMIT_REFRESH":
        raise SystemExit(
            "Workflow moved to an unexpected state before pre-submit: "
            f"status={status!r}"
        )

    run_stage(
        "FRESH PRE-SUBMIT REVIEW",
        presubmit_command,
        cwd=repo,
    )

    run_stage(
        "DRY-RUN SUBMISSION PACKAGE",
        [
            python,
            "scripts/run_v1_submit.py",
            "--as-of",
            args.as_of.isoformat(),
            "--repo-root",
            repo,
        ],
        cwd=repo,
    )

    snapshot = approval_snapshot(run_dir)
    print_approval_checkpoint(
        as_of=args.as_of,
        snapshot=snapshot,
        warnings=warnings,
    )

    if not prompt_for_approval():
        print()
        print("=" * 72)
        print("NOT APPROVED")
        print("No live orders were placed by the interactive pipeline.")
        print("Rerun the same command to refresh the approval package and resume.")
        print("=" * 72)
        return

    try:
        run_stage(
            "APPROVED LIVE SUBMISSION",
            [
                python,
                "scripts/run_v1_submit.py",
                "--as-of",
                args.as_of.isoformat(),
                "--repo-root",
                repo,
                "--approve",
            ],
            cwd=repo,
        )
    except subprocess.CalledProcessError:
        failed_status = workflow_status(run_dir)
        if failed_status == "SUBMISSION_REQUIRES_RECONCILIATION":
            print(
                "Approved submission requires reconciliation. Running the "
                "read-only recovery path; no new placement call will be made.",
                flush=True,
            )
            run_stage(
                "READ-ONLY SUBMISSION RECOVERY",
                [
                    python,
                    "scripts/recover_v1_submission.py",
                    "--as-of",
                    args.as_of.isoformat(),
                    "--repo-root",
                    repo,
                ],
                cwd=repo,
            )
        else:
            raise

    final_status = workflow_status(run_dir) or "UNKNOWN"
    if final_status == "SUBMITTED_RECONCILED":
        final_status = run_postfill_resume(
            repo=repo,
            as_of=args.as_of,
            python=python,
        )

    print()
    print("=" * 72)
    print("INTERACTIVE LIVE PIPELINE FINISHED")
    print(f"Workflow status:             {final_status}")
    if final_status == "POSTFILL_PENDING":
        print("Rerun this same one-command pipeline to refresh fills.")
    elif final_status == "SUBMISSION_REQUIRES_RECONCILIATION":
        print("STOP: submission remains ambiguous. Do not resubmit.")
    elif final_status == "POSTFILL_RECONCILIATION_REQUIRED":
        print(
            "Post-fill reconciliation still requires attention. Rerunning the "
            "same pipeline is read-only and will retry post-fill verification."
        )
    elif final_status == "COMPLETE":
        print("Weekly live workflow is complete.")
    print("=" * 72)


if __name__ == "__main__":
    main()
