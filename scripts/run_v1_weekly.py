from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from finance.shadow.weekly_workflow import build_weekly_workflow_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan the V1 weekly Agentic investing workflow without placing orders."
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--report-root", type=Path, default=Path("reports/shadow"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Plan only. No broker action or command execution.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_weekly_workflow_plan(
        as_of=args.as_of,
        repo_root=args.repo_root,
        report_root=args.report_root,
        dry_run=True,
    )

    run_dir = Path(plan.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_dir / "workflow_plan.json"
    manifest.write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V1 WEEKLY WORKFLOW DRY RUN")
    print(f"As of:      {plan.as_of}")
    print(f"Run dir:    {plan.run_dir}")
    print(f"Run log:    {plan.run_log}")
    print(f"Manifest:   {manifest}")
    print()
    for index, stage in enumerate(plan.stages, start=1):
        print(f"{index:>2}. {stage.name} [{stage.mode}]")
        print(f"    {stage.description}")
        if stage.command:
            print(f"    command: {stage.command}")
        if stage.outputs:
            for output in stage.outputs:
                print(f"    output:  {output}")
        print()
    print("DRY RUN ONLY: no commands were executed and no broker action was taken.")


if __name__ == "__main__":
    main()
