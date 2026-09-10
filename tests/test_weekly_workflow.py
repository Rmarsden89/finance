from datetime import date
from pathlib import Path

from finance.shadow.weekly_workflow import build_weekly_workflow_plan


def test_weekly_workflow_paths_are_dated_and_local(tmp_path) -> None:
    plan = build_weekly_workflow_plan(
        as_of=date(2026, 9, 10),
        repo_root=tmp_path,
    )

    run_dir = Path(plan.run_dir)
    assert run_dir == tmp_path / "reports" / "shadow" / "2026-09-10"
    assert Path(plan.run_log) == run_dir / "run_log.jsonl"
    assert plan.dry_run

    names = [stage.name for stage in plan.stages]
    assert names == [
        "tests",
        "current_model_refresh",
        "broker_snapshot_pre",
        "normalize_broker_state",
        "shadow_decision",
        "execution_gate",
        "order_intents",
        "broker_snapshot_presubmit",
        "pre_submit_gate",
        "submission",
        "submission_reconciliation",
        "broker_snapshot_postfill",
        "post_fill_reconciliation",
    ]


def test_submission_is_explicit_external_trade_stage(tmp_path) -> None:
    plan = build_weekly_workflow_plan(
        as_of=date(2026, 9, 10),
        repo_root=tmp_path,
    )

    submission = next(stage for stage in plan.stages if stage.name == "submission")
    assert submission.mode == "external_trade"
    assert submission.command is None
    assert submission.outputs[0].endswith("submission_receipt.json")
