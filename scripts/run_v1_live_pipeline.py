from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the normal long_growth_v1 live workflow as one interactive pipeline. "
            "Existing stage scripts remain authoritative for all safety gates and broker actions."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--sec-user-agent",
        default=None,
        help=(
            "Optional SEC_USER_AGENT value for this run. If omitted, the existing "
            "SEC_USER_AGENT environment variable is used."
        ),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_stage(
    label: str,
    command: Sequence[str],
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


def main() -> None:
    args = parse_args()
    repo = args.repo_root.resolve()
    run_dir = repo / "reports" / "shadow" / args.as_of.isoformat()
    python = sys.executable

    if args.sec_user_agent:
        os.environ["SEC_USER_AGENT"] = args.sec_user_agent
    if not os.environ.get("SEC_USER_AGENT", "").strip():
        raise SystemExit(
            "SEC_USER_AGENT is required. Set it in the environment or pass "
            "--sec-user-agent for this run."
        )

    warnings: list[str] = []

    run_stage(
        "1/6 - V1 PREPARE",
        [python, "scripts/run_v1_prepare.py", "--as-of", args.as_of.isoformat(), "--repo-root", repo],
        cwd=repo,
    )

    v2_ok = run_stage(
        "2/6 - V2 RESEARCH SHADOW",
        [python, "scripts/run_v2_ttm_shadow.py", "--as-of", args.as_of.isoformat(), "--repo-root", repo],
        cwd=repo,
        required=False,
    )
    if not v2_ok:
        warnings.append("V2 shadow observation failed and does not count for this week.")

    if v2_ok:
        v3_ok = run_stage(
            "3/6 - V3 RESEARCH SHADOW",
            [python, "scripts/run_v3_shadow.py", "--as-of", args.as_of.isoformat(), "--repo-root", repo],
            cwd=repo,
            required=False,
        )
        if not v3_ok:
            warnings.append("V3 shadow observation failed and does not count for this week.")
    else:
        print()
        print(
            "Skipping V3 shadow because V3 depends on a valid V2 shadow observation.",
            flush=True,
        )
        warnings.append("V3 shadow was skipped because V2 did not complete successfully.")

    run_stage(
        "4/6 - FRESH PRE-SUBMIT REVIEW",
        [python, "scripts/run_v1_presubmit.py", "--as-of", args.as_of.isoformat(), "--repo-root", repo],
        cwd=repo,
    )

    run_stage(
        "5/6 - DRY-RUN SUBMISSION PACKAGE",
        [python, "scripts/run_v1_submit.py", "--as-of", args.as_of.isoformat(), "--repo-root", repo],
        cwd=repo,
    )

    snapshot = approval_snapshot(run_dir)
    print_approval_checkpoint(as_of=args.as_of, snapshot=snapshot, warnings=warnings)

    if not prompt_for_approval():
        print()
        print("=" * 72)
        print("NOT APPROVED")
        print("No live orders were placed by the interactive pipeline.")
        print("=" * 72)
        return

    run_stage(
        "6/6 - APPROVED LIVE SUBMISSION",
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

    state_path = run_dir / "workflow_state.json"
    state = read_json(state_path) if state_path.exists() else {}
    status = str(state.get("status") or "UNKNOWN")
    print()
    print("=" * 72)
    print("INTERACTIVE LIVE PIPELINE FINISHED")
    print(f"Workflow status:             {status}")
    if status == "POSTFILL_PENDING":
        print(
            "Next: py scripts\\run_v1_postfill.py "
            f"--as-of {args.as_of.isoformat()}"
        )
    elif status == "SUBMISSION_REQUIRES_RECONCILIATION":
        print(
            "STOP: do not rerun approved submission. Use "
            f"py scripts\\recover_v1_submission.py --as-of {args.as_of.isoformat()}"
        )
    elif status == "POSTFILL_RECONCILIATION_REQUIRED":
        print("STOP: inspect the saved post-fill reconciliation artifacts.")
    print("=" * 72)


if __name__ == "__main__":
    main()
