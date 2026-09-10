import json

import pytest

from finance.shadow.run_log import artifact_record, logged_stage


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
