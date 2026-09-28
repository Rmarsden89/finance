from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_model_comparison.py"
SPEC = importlib.util.spec_from_file_location("build_model_comparison", SCRIPT)
comparison = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(comparison)


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def rows(first: str = "A") -> list[dict]:
    tickers = [first] + [f"T{n}" for n in range(2, 11)]
    return [
        {"rank": rank, "ticker": ticker, "score": 90 - rank}
        for rank, ticker in enumerate(tickers, start=1)
    ]


def model() -> dict:
    return {
        "id": "future_challenger",
        "summary": "research/{as_of}/summary.json",
        "decision": "research/{as_of}/decision.json",
        "expected_status": "FUTURE_COMPLETE",
        "decision_hash_field": "future_decision_hash",
    }


def create_run(root: Path, date_text: str, *, shadow: bool = True) -> None:
    live = root / "reports" / "shadow" / date_text
    save(live / "workflow_state.json", {"status": "COMPLETE"})
    save(live / "shadow_decision.json", {
        "decision_hash": f"v1-{date_text}",
        "decisions": rows("AAA"),
    })
    if shadow:
        shadow_root = root / "research" / date_text
        save(shadow_root / "summary.json", {
            "as_of": date_text,
            "status": "FUTURE_COMPLETE",
            "shadow_week_valid": True,
            "pit_violations": 0,
            "v1_decision_hash": f"v1-{date_text}",
            "future_decision_hash": f"future-{date_text}",
            "execution_capabilities": {
                "broker_access": False,
                "order_intents": False,
                "order_review": False,
                "order_placement": False,
            },
        })
        save(shadow_root / "decision.json", {
            "as_of": date_text,
            "v1_decision_hash": f"v1-{date_text}",
            "decision_hash": f"future-{date_text}",
            "top10": rows("BBB"),
            "execution_capabilities": {
                "broker_access": False,
                "order_intents": False,
                "order_review": False,
                "order_placement": False,
            },
        })


def test_iso_week_count_is_distinct_from_observation_count(tmp_path: Path) -> None:
    create_run(tmp_path, "2026-09-25")
    create_run(tmp_path, "2026-09-28")
    create_run(tmp_path, "2026-09-29")
    observations, selections, result = comparison.build_comparison(
        tmp_path, [model()]
    )
    future = result["summary"]["models"]["future_challenger"]
    assert future["valid_observation_dates"] == 3
    assert future["distinct_qualifying_weeks"] == 2
    assert not future["formal_review_ready"]
    assert len(selections) == 60
    assert len(observations) == 6
    assert all(row["overlap"] == 9 for row in result["pairwise"])


def test_eight_distinct_weeks_trigger_review_only(tmp_path: Path) -> None:
    for day in (
        "2026-09-25", "2026-09-28", "2026-10-05", "2026-10-12",
        "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09",
    ):
        create_run(tmp_path, day)
    _, _, result = comparison.build_comparison(tmp_path, [model()])
    future = result["summary"]["models"]["future_challenger"]
    assert future["distinct_qualifying_weeks"] == 8
    assert future["formal_review_ready"]
    assert result["summary"]["live_promotion_authorized"] is False


def test_unmatched_shadow_hash_excluded_and_reported(tmp_path: Path) -> None:
    create_run(tmp_path, "2026-09-28")
    path = tmp_path / "research" / "2026-09-28" / "summary.json"
    payload = comparison.read_json(path)
    payload["v1_decision_hash"] = "different"
    save(path, payload)
    observations, selections, result = comparison.build_comparison(
        tmp_path, [model()]
    )
    assert len(observations) == 1
    assert len(selections) == 10
    assert result["summary"]["models"]["future_challenger"]["distinct_qualifying_weeks"] == 0
    assert "hash mismatch" in result["summary"]["invalid_or_incomplete_shadow_artifacts"][0]["reason"].lower()


def test_empty_or_duplicate_top10_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Expected unique ranks"):
        comparison.decision_top10({"top10": rows("T2")}, source=tmp_path)


def test_model_agnostic_registry_requires_decision_artifact(tmp_path: Path) -> None:
    save(tmp_path / "registry.json", {
        "schema_version": 1,
        "modes": [{
            "id": "v4",
            "summary": "research/{as_of}/summary.json",
            "decision": "research/{as_of}/decision.json",
            "expected_status": "V4_DONE",
        }],
    })
    assert comparison.get_registry(tmp_path, Path("registry.json"))[0]["id"] == "v4"
