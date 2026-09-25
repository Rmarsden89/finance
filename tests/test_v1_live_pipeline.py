from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


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


def test_nonblocking_stage_failure_returns_false(monkeypatch, tmp_path) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, ["python", "shadow.py"])

    monkeypatch.setattr(pipeline.subprocess, "run", fail)
    assert not pipeline.run_stage(
        "V2 research shadow",
        ["python", "shadow.py"],
        cwd=tmp_path,
        required=False,
    )


def test_required_stage_failure_is_raised(monkeypatch, tmp_path) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, ["python", "prepare.py"])

    monkeypatch.setattr(pipeline.subprocess, "run", fail)

    try:
        pipeline.run_stage(
            "V1 prepare",
            ["python", "prepare.py"],
            cwd=tmp_path,
            required=True,
        )
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("required live stage failure must stop the pipeline")


def test_approval_snapshot_reads_frozen_package(tmp_path) -> None:
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
    assert [row["ticker"] for row in snapshot["orders"]] == ["AAA", "BBB"]


def test_completed_research_shadow_accepts_valid_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
                "shadow_week_valid": True,
                "pit_violations": 0,
                "v1_decision_hash": "abc123",
            }
        ),
        encoding="utf-8",
    )

    assert PIPELINE.completed_research_shadow(
        summary,
        "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
        expected_v1_decision_hash="abc123",
    )


def test_completed_research_shadow_rejects_invalid_or_pit_violating_summary(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
                "shadow_week_valid": True,
                "pit_violations": 1,
            }
        ),
        encoding="utf-8",
    )

    assert not PIPELINE.completed_research_shadow(
        summary,
        "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
        expected_v1_decision_hash="abc123",
    )


def test_completed_research_shadow_rejects_v1_hash_mismatch(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
                "shadow_week_valid": True,
                "pit_violations": 0,
                "v1_decision_hash": "old-hash",
            }
        ),
        encoding="utf-8",
    )

    assert not PIPELINE.completed_research_shadow(
        summary,
        "V2_TTM_SHADOW_OBSERVATION_COMPLETE",
        expected_v1_decision_hash="new-hash",
    )


def test_main_skips_v3_when_v2_shadow_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "test@example.com")
    monkeypatch.setattr(
        pipeline,
        "parse_args",
        lambda: SimpleNamespace(
            as_of=date(2026, 9, 25),
            repo_root=tmp_path,
            sec_user_agent=None,
        ),
    )

    calls = []

    def fake_run_stage(label, command, *, cwd, required=True):
        calls.append((label, list(command), required))
        if "V2 RESEARCH SHADOW" in label:
            return False
        return True

    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)
    monkeypatch.setattr(
        pipeline,
        "approval_snapshot",
        lambda run_dir: {
            "decision_hash": "abc",
            "order_count": 1,
            "total_dollars": 1.0,
            "orders": [{"ticker": "AAA", "amount_dollars": 1.0, "order_type": "market", "market_hours": "regular_hours"}],
            "snapshot_age_minutes": 0.1,
            "buying_power": 100.0,
            "tradable_count": 1,
            "gate_order_count": 1,
            "reviews_clean": 1,
            "reviews_count": 1,
        },
    )
    monkeypatch.setattr(pipeline, "print_approval_checkpoint", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "prompt_for_approval", lambda: False)

    pipeline.main()

    labels = [label for label, _, _ in calls]
    assert labels == [
        "1/6 - V1 PREPARE",
        "2/6 - V2 RESEARCH SHADOW",
        "4/6 - FRESH PRE-SUBMIT REVIEW",
        "5/6 - DRY-RUN SUBMISSION PACKAGE",
    ]


def test_main_only_invokes_approved_submit_after_yes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "test@example.com")
    monkeypatch.setattr(
        pipeline,
        "parse_args",
        lambda: SimpleNamespace(
            as_of=date(2026, 9, 25),
            repo_root=tmp_path,
            sec_user_agent=None,
        ),
    )

    run_dir = tmp_path / "reports" / "shadow" / "2026-09-25"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow_state.json").write_text(
        json.dumps({"status": "POSTFILL_PENDING"}),
        encoding="utf-8",
    )

    calls = []

    def fake_run_stage(label, command, *, cwd, required=True):
        calls.append((label, list(command), required))
        return True

    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)
    monkeypatch.setattr(
        pipeline,
        "approval_snapshot",
        lambda run_dir: {
            "decision_hash": "abc",
            "order_count": 1,
            "total_dollars": 1.0,
            "orders": [{"ticker": "AAA", "amount_dollars": 1.0, "order_type": "market", "market_hours": "regular_hours"}],
            "snapshot_age_minutes": 0.1,
            "buying_power": 100.0,
            "tradable_count": 1,
            "gate_order_count": 1,
            "reviews_clean": 1,
            "reviews_count": 1,
        },
    )
    monkeypatch.setattr(pipeline, "print_approval_checkpoint", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "prompt_for_approval", lambda: True)

    pipeline.main()

    approved = [command for label, command, _ in calls if "APPROVED LIVE SUBMISSION" in label]
    assert len(approved) == 1
    assert approved[0][-1] == "--approve"
