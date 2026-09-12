import json

import pytest

from finance.shadow.run_log import artifact_record, append_run_event, logged_stage


def test_logged_stage_records_success_and_failure(tmp_path) -> None:
    log_path = tmp_path / "run_log.jsonl"
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("hello", encoding="utf-8")

    with logged_stage(log_path, "success_stage", inputs={"x": 1}) as details:
        details["artifact"] = artifact_record(artifact)

    with pytest.raises(ValueError):
        with logged_stage(log_path, "failed_stage"):
            raise ValueError("boom")

    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["status"] == "success"
    assert rows[0]["details"]["artifact"]["sha256"]
    assert rows[1]["status"] == "failed"
    assert rows[1]["error_type"] == "ValueError"


def test_run_events_inherit_run_and_attempt_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FINANCE_RUN_ID", "long_growth_v1-2026-09-12")
    monkeypatch.setenv("FINANCE_ATTEMPT_ID", "attempt-123")
    log_path = tmp_path / "run_log.jsonl"

    append_run_event(log_path, {"stage": "example", "status": "success"})

    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["run_id"] == "long_growth_v1-2026-09-12"
    assert row["attempt_id"] == "attempt-123"
