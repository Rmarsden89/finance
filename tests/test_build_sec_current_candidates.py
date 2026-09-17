from pathlib import Path

import pytest

from scripts.build_sec_current_candidates import validate_output_isolation


def test_v1_default_outputs_are_not_restricted_to_v2_namespace(tmp_path: Path) -> None:
    validate_output_isolation(
        policy="v1_exact_only",
        output=tmp_path / "reports" / "sec_current_candidate_facts.csv",
        audit_output=tmp_path / "reports" / "sec_current_candidate_audit.csv",
        repo_root=tmp_path,
    )


def test_v2_policy_accepts_only_isolated_research_outputs(tmp_path: Path) -> None:
    run_dir = (
        tmp_path
        / "reports"
        / "v2"
        / "long_growth_v2_research"
        / "2026-09-15"
    )
    validate_output_isolation(
        policy="v2_dei_cover_date",
        output=run_dir / "sec_current_candidate_facts.csv",
        audit_output=run_dir / "sec_current_candidate_audit.csv",
        repo_root=tmp_path,
    )


def test_v2_policy_rejects_normal_v1_report_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="V2 candidate outputs must stay under"):
        validate_output_isolation(
            policy="v2_dei_cover_date",
            output=tmp_path / "reports" / "sec_current_candidate_facts.csv",
            audit_output=tmp_path / "reports" / "sec_current_candidate_audit.csv",
            repo_root=tmp_path,
        )
