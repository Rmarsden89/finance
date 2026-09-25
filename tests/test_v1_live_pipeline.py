from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_v1_live_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_v1_live_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pipeline)


def test_prompt_for_approval_only_accepts_explicit_y(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert pipeline.prompt_for_approval()

    monkeypatch.setattr("builtins.input", lambda _: "Y")
    assert pipeline.prompt_for_approval()

    for value in ("", "n", "yes", "approve"):
        monkeypatch.setattr("builtins.input", lambda _, value=value: value)
        assert not pipeline.prompt_for_approval()


def test_prompt_for_approval_fails_safe_on_eof(monkeypatch) -> None:
    def raise_eof(_: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert not pipeline.prompt_for_approval()


@pytest.mark.parametrize(
    ("status", "receipt_exists", "expected"),
    [
        (None, False, "prepare"),
        ("FAILED", False, "prepare"),
        ("RUNNING", False, "prepare"),
        ("READY_FOR_PRESUBMIT_REFRESH", False, "continue"),
        ("AWAITING_APPROVAL", False, "continue"),
        ("PRESUBMIT_RUNNING", False, "continue"),
        ("PRESUBMIT_BLOCKED", False, "continue"),
        ("PRESUBMIT_FAILED", False, "continue"),
        ("SUBMISSION_REQUIRES_RECONCILIATION", True, "recover_submission"),
        ("SUBMISSION_RUNNING", True, "recover_submission"),
        ("SUBMISSION_RUNNING", False, "stop_ambiguous_submission"),
        ("SUBMITTED_RECONCILED", True, "postfill"),
        ("POSTFILL_RUNNING", True, "postfill"),
        ("POSTFILL_PENDING", True, "postfill"),
        ("POSTFILL_RECONCILIATION_REQUIRED", True, "postfill"),
        ("COMPLETE", True, "complete"),
        ("UNKNOWN_STATE", False, "stop_unknown"),
    ],
)
def test_workflow_action_is_fail_closed(
    status: str | None,
    receipt_exists: bool,
    expected: str,
) -> None:
    assert pipeline.workflow_action(
        status,
        receipt_exists=receipt_exists,
    ) == expected


def test_nonblocking_stage_failure_returns_false(monkeypatch, tmp_path) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, ["python", "shadow.py"])

    monkeypatch.setattr(pipeline.subprocess, "run", fail)
    assert not pipeline.run_stage(
        "research shadow",
        ["python", "shadow.py"],
        cwd=tmp_path,
        required=False,
    )


def test_required_stage_failure_is_raised(monkeypatch, tmp_path) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, ["python", "prepare.py"])

    monkeypatch.setattr(pipeline.subprocess, "run", fail)

    with pytest.raises(subprocess.CalledProcessError):
        pipeline.run_stage(
            "V1 prepare",
            ["python", "prepare.py"],
            cwd=tmp_path,
            required=True,
        )


def test_registry_orders_dependencies(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "modes": [
                    {
                        "id": "v3",
                        "label": "V3",
                        "runner": "v3.py",
                        "summary": "v3/{as_of}/summary.json",
                        "expected_status": "V3_DONE",
                        "depends_on": ["v2"],
                    },
                    {
                        "id": "v2",
                        "label": "V2",
                        "runner": "v2.py",
                        "summary": "v2/{as_of}/summary.json",
                        "expected_status": "V2_DONE",
                        "depends_on": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    modes = pipeline.load_shadow_registry(tmp_path, registry)

    assert [mode["id"] for mode in modes] == ["v2", "v3"]


def test_registry_rejects_dependency_cycle(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "modes": [
                    {
                        "id": "a",
                        "label": "A",
                        "runner": "a.py",
                        "summary": "a/{as_of}/summary.json",
                        "expected_status": "A_DONE",
                        "depends_on": ["b"],
                    },
                    {
                        "id": "b",
                        "label": "B",
                        "runner": "b.py",
                        "summary": "b/{as_of}/summary.json",
                        "expected_status": "B_DONE",
                        "depends_on": ["a"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="dependency cycle"):
        pipeline.load_shadow_registry(tmp_path, registry)


def _shadow_mode() -> dict:
    return {
        "id": "v2",
        "label": "V2",
        "runner": "v2.py",
        "summary": "reports/v2/{as_of}/summary.json",
        "work_dir": "reports/v2/{as_of}",
        "expected_status": "V2_DONE",
        "depends_on": [],
        "non_blocking": True,
        "require_v1_decision_hash": True,
        "require_zero_pit_violations": True,
        "require_shadow_week_valid": True,
    }


def _write_shadow_summary(
    tmp_path: Path,
    *,
    decision_hash: str = "abc",
    pit_violations: int = 0,
    valid: bool = True,
) -> Path:
    path = tmp_path / "reports" / "v2" / "2026-09-25" / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "V2_DONE",
                "v1_decision_hash": decision_hash,
                "pit_violations": pit_violations,
                "shadow_week_valid": valid,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_shadow_completion_requires_active_v1_hash_and_pit_validity(
    tmp_path: Path,
) -> None:
    mode = _shadow_mode()
    _write_shadow_summary(tmp_path)

    assert pipeline.shadow_observation_state(
        mode,
        repo=tmp_path,
        as_of=date(2026, 9, 25),
        expected_v1_decision_hash="abc",
    ) == "complete"

    assert pipeline.shadow_observation_state(
        mode,
        repo=tmp_path,
        as_of=date(2026, 9, 25),
        expected_v1_decision_hash="different",
    ) == "hash_mismatch"

    _write_shadow_summary(tmp_path, pit_violations=1)
    assert pipeline.shadow_observation_state(
        mode,
        repo=tmp_path,
        as_of=date(2026, 9, 25),
        expected_v1_decision_hash="abc",
    ) == "invalid"


def test_partial_shadow_work_is_archived_not_deleted(tmp_path: Path) -> None:
    mode = _shadow_mode()
    work_dir = tmp_path / "reports" / "v2" / "2026-09-25"
    work_dir.mkdir(parents=True)
    evidence = work_dir / "partial.txt"
    evidence.write_text("failure evidence", encoding="utf-8")

    archived = pipeline.archive_partial_shadow_work(
        mode,
        repo=tmp_path,
        as_of=date(2026, 9, 25),
    )

    assert archived is not None
    assert not work_dir.exists()
    assert (archived / "partial.txt").read_text(encoding="utf-8") == "failure evidence"


def test_approval_snapshot_reads_frozen_package(tmp_path: Path) -> None:
    run_dir = tmp_path / "reports" / "shadow" / "2026-09-25"
    run_dir.mkdir(parents=True)
    (run_dir / "order_intents.json").write_text(
        json.dumps(
            {
                "decision_hash": "abc",
                "order_count": 2,
                "total_dollars": 2.0,
                "intents": [
                    {
                        "ticker": "AAA",
                        "amount_dollars": 1.0,
                        "order_type": "market",
                        "market_hours": "regular_hours",
                    },
                    {
                        "ticker": "BBB",
                        "amount_dollars": 1.0,
                        "order_type": "market",
                        "market_hours": "regular_hours",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "pre_submit_gate.json").write_text(
        json.dumps(
            {
                "snapshot_created_at": "2026-09-25T12:00:00Z",
                "buying_power": 100.0,
                "tradable_count": 2,
                "order_count": 2,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "order_reviews.json").write_text(
        json.dumps(
            {
                "validation": {
                    "clean_count": 2,
                    "reviewed_count": 2,
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = pipeline.approval_snapshot(run_dir)

    assert snapshot["decision_hash"] == "abc"
    assert snapshot["order_count"] == 2
    assert snapshot["total_dollars"] == 2.0
    assert snapshot["buying_power"] == 100.0
    assert snapshot["tradable_count"] == 2
    assert snapshot["reviews_clean"] == 2


def test_complete_run_exits_without_running_any_stage(monkeypatch, tmp_path) -> None:
    run_dir = tmp_path / "reports" / "shadow" / "2026-09-25"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow_state.json").write_text(
        json.dumps({"status": "COMPLETE"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline,
        "parse_args",
        lambda: SimpleNamespace(
            as_of=date(2026, 9, 25),
            repo_root=tmp_path,
            shadow_registry=Path("config/live_shadow_modes.json"),
            sec_user_agent=None,
        ),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("No stage should run for a COMPLETE workflow")

    monkeypatch.setattr(pipeline, "run_stage", fail_if_called)

    pipeline.main()


def test_interrupted_submission_without_receipt_is_hard_stop(
    monkeypatch,
    tmp_path,
) -> None:
    run_dir = tmp_path / "reports" / "shadow" / "2026-09-25"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow_state.json").write_text(
        json.dumps({"status": "SUBMISSION_RUNNING"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline,
        "parse_args",
        lambda: SimpleNamespace(
            as_of=date(2026, 9, 25),
            repo_root=tmp_path,
            shadow_registry=Path("config/live_shadow_modes.json"),
            sec_user_agent=None,
        ),
    )

    with pytest.raises(SystemExit, match="no saved broker receipt"):
        pipeline.main()


def test_recovery_scripts_accept_only_safe_interrupted_states() -> None:
    root = Path(__file__).parents[1]
    presubmit = (root / "scripts" / "run_v1_presubmit.py").read_text(
        encoding="utf-8"
    )
    recovery = (root / "scripts" / "recover_v1_submission.py").read_text(
        encoding="utf-8"
    )
    postfill = (root / "scripts" / "run_v1_postfill.py").read_text(
        encoding="utf-8"
    )

    assert '"PRESUBMIT_RUNNING"' in presubmit
    assert '"PRESUBMIT_BLOCKED"' in presubmit
    assert '"PRESUBMIT_FAILED"' in presubmit
    assert '"SUBMISSION_RUNNING"' in recovery
    assert '"POSTFILL_RUNNING"' in postfill
