from datetime import date, datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from finance.models.long_growth_v1 import add_long_growth_v1_scores
from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    V2ResearchConfig,
    build_v2_research_manifest,
    resolve_v2_run_dir,
    resolve_v2_impact_artifact_paths,
    resolve_v2_liabilities_audit_paths,
    resolve_v2_share_cleanup_paths,
    resolve_v2_sec_artifact_paths,
    write_v2_research_manifest,
)


def test_v2_artifacts_use_an_isolated_namespace(tmp_path: Path) -> None:
    run_dir = resolve_v2_run_dir(tmp_path, date(2026, 9, 16))

    assert run_dir == (
        tmp_path
        / "reports"
        / "v2"
        / "long_growth_v2_research"
        / "2026-09-16"
    )
    assert tmp_path / "reports" / "shadow" not in run_dir.parents


def test_v2_manifest_records_provenance_and_disables_execution(tmp_path: Path) -> None:
    manifest = build_v2_research_manifest(
        repo_root=tmp_path,
        as_of=date(2026, 9, 16),
        data_baseline_id="v1-hardened-844d160",
        created_at=datetime(2026, 9, 16, 2, 30, tzinfo=timezone.utc),
    )

    assert manifest["model_id"] == "long_growth_v2_research"
    assert manifest["source_champion"] == "long_growth_v1"
    assert manifest["decision_date"] == "2026-09-16"
    assert manifest["data_baseline_id"] == "v1-hardened-844d160"
    assert manifest["configuration_hash"] == LONG_GROWTH_V2_RESEARCH.configuration_hash
    assert manifest["execution_capabilities"] == {
        "broker_access": False,
        "order_intents": False,
        "order_review": False,
        "order_placement": False,
    }
    assert manifest["data_capabilities"] == {
        "dei_exact_share_fallback": True,
        "dei_cover_date_fallback": True,
    }
    assert manifest["allowed_stages"] == [
        "research_manifest",
        "sec_candidate_build",
        "sec_shadow_merge",
        "current_shadow_panel",
        "same_input_impact_comparison",
        "residual_shares_cleanup_audit",
        "targeted_sec_gap_refresh",
        "liabilities_gap_audit",
    ]


@pytest.mark.parametrize(
    "field",
    [
        "broker_access_enabled",
        "order_intents_enabled",
        "order_review_enabled",
        "order_placement_enabled",
    ],
)
def test_v2_rejects_every_execution_capability(field: str) -> None:
    values = {field: True}
    config = V2ResearchConfig(**values)

    with pytest.raises(ValueError, match="cannot enable execution capabilities"):
        config.validate()


def test_v2_manifest_write_does_not_create_v1_or_order_artifacts(tmp_path: Path) -> None:
    output = write_v2_research_manifest(
        repo_root=tmp_path,
        as_of=date(2026, 9, 16),
        data_baseline_id="v1-hardened-844d160",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "RESEARCH_INITIALIZED"
    assert output.name == "research_manifest.json"
    assert not (tmp_path / "reports" / "shadow").exists()
    assert not list(tmp_path.rglob("order_intents.json"))


def test_v2_scaffold_does_not_change_frozen_v1_scoring() -> None:
    frame = pd.DataFrame(
        {
            "decision_date": ["2026-09-11", "2026-09-11"],
            "quality_score": [80.0, 90.0],
            "financial_health_score": [80.0, None],
            "growth_score": [80.0, 70.0],
            "valuation_score": [80.0, 50.0],
        }
    )

    result = add_long_growth_v1_scores(frame)

    assert result["model_id"].tolist() == ["long_growth_v1", "long_growth_v1"]
    assert result["long_growth_v1_score"].tolist() == [80.0, 73.75]
    assert result["top_conviction_eligible"].tolist() == [True, False]


def test_v2_sec_artifacts_are_all_inside_the_dated_run_directory(
    tmp_path: Path,
) -> None:
    paths = resolve_v2_sec_artifact_paths(tmp_path, date(2026, 9, 15))
    run_dir = resolve_v2_run_dir(tmp_path, date(2026, 9, 15))

    assert paths["run_dir"] == run_dir
    assert all(
        path == run_dir or run_dir in path.parents
        for path in paths.values()
    )
    assert not any(
        tmp_path / "reports" / "shadow" in path.parents
        for path in paths.values()
    )


def test_v2_impact_artifacts_are_isolated_from_v1_paths(tmp_path: Path) -> None:
    paths = resolve_v2_impact_artifact_paths(tmp_path, date(2026, 9, 15))
    run_dir = resolve_v2_run_dir(tmp_path, date(2026, 9, 15))

    assert paths["impact_dir"] == run_dir / "impact"
    assert all(
        path == run_dir or run_dir in path.parents
        for path in paths.values()
    )
    assert not any(
        tmp_path / "reports" / "shadow" in path.parents
        for path in paths.values()
    )


def test_v2_share_cleanup_artifacts_are_isolated(tmp_path: Path) -> None:
    paths = resolve_v2_share_cleanup_paths(tmp_path, date(2026, 9, 15))
    run_dir = resolve_v2_run_dir(tmp_path, date(2026, 9, 15))

    assert paths["cleanup_dir"] == run_dir / "cleanup"
    assert all(run_dir in path.parents for path in paths.values())


def test_v2_liabilities_audit_artifacts_are_isolated(tmp_path: Path) -> None:
    paths = resolve_v2_liabilities_audit_paths(tmp_path, date(2026, 9, 15))
    run_dir = resolve_v2_run_dir(tmp_path, date(2026, 9, 15))

    assert paths["audit_dir"] == run_dir / "liabilities"
    assert all(run_dir in path.parents for path in paths.values())


def test_v2_rejects_cover_date_fallback_without_exact_dei_fallback() -> None:
    config = V2ResearchConfig(
        dei_exact_share_fallback_enabled=False,
        dei_cover_date_fallback_enabled=True,
    )

    with pytest.raises(ValueError, match="requires exact DEI fallback"):
        config.validate()


def test_v2_sec_runner_has_no_broker_or_order_module_imports() -> None:
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_v2_sec_research.py"
    ).read_text(encoding="utf-8")

    prohibited = (
        "finance.broker",
        "finance.shadow.order_intent",
        "finance.shadow.order_review",
        "place_equity_order",
    )
    assert not any(value in source for value in prohibited)
