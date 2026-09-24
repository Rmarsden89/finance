from __future__ import annotations

from datetime import date
import json

from finance.research.v2 import find_latest_prior_v2_run


def _write_run(root, day: str, *, status: str) -> None:
    run = (
        root
        / "reports"
        / "v2"
        / "long_growth_v2_research"
        / day
    )
    run.mkdir(parents=True)
    (run / "research_manifest.json").write_text(
        json.dumps({"status": status}),
        encoding="utf-8",
    )


def test_find_latest_prior_v2_run_returns_latest_completed(tmp_path) -> None:
    _write_run(tmp_path, "2026-09-15", status="SEC_RESEARCH_COMPLETE")
    _write_run(tmp_path, "2026-09-20", status="FAILED")
    _write_run(tmp_path, "2026-09-22", status="SEC_RESEARCH_COMPLETE")

    found = find_latest_prior_v2_run(
        tmp_path,
        date(2026, 9, 23),
    )

    assert found is not None
    assert found[0] == date(2026, 9, 22)
    assert found[1].name == "2026-09-22"


def test_find_latest_prior_v2_run_excludes_same_or_future_date(tmp_path) -> None:
    _write_run(tmp_path, "2026-09-23", status="SEC_RESEARCH_COMPLETE")
    _write_run(tmp_path, "2026-09-24", status="SEC_RESEARCH_COMPLETE")

    assert find_latest_prior_v2_run(
        tmp_path,
        date(2026, 9, 23),
    ) is None
