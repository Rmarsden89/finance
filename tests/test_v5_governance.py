from __future__ import annotations

import copy

import pytest

from finance.research.v5_governance import (
    ExperimentRecord,
    canonical_json_sha256,
    validate_v5_protocol,
)


def protocol_fixture() -> dict:
    return {
        "protocol_id": "v5_evaluation_protocol_v1",
        "status": "frozen",
        "frozen_before_final_candidate_review": True,
        "primary_objective": {"name": "matched_cash_flow_terminal_value"},
        "hard_gates": {
            "economic": {
                "minimum_terminal_value_improvement_pct": 0.01,
                "minimum_xirr_delta": 0.001,
                "require_both_terminal_value_and_xirr_gates": True,
            }
        },
        "decision_rule": {
            "historical_pass_authorizes_live_trading": False,
            "explicit_human_promotion_decision_required": True,
        },
        "experiment_governance": {
            "frozen_candidate_config_may_not_change_in_place": True,
            "rejected_and_negative_results_must_be_retained": True,
            "unrestricted_grid_search_prohibited": True,
        },
    }


def test_protocol_hash_is_deterministic() -> None:
    payload = protocol_fixture()
    assert canonical_json_sha256(payload) == canonical_json_sha256(copy.deepcopy(payload))


def test_protocol_rejects_live_auto_promotion() -> None:
    payload = protocol_fixture()
    payload["decision_rule"]["historical_pass_authorizes_live_trading"] = True
    with pytest.raises(ValueError, match="may not authorize live trading"):
        validate_v5_protocol(payload)


def test_protocol_rejects_nonpositive_economic_gate() -> None:
    payload = protocol_fixture()
    payload["hard_gates"]["economic"]["minimum_terminal_value_improvement_pct"] = 0
    with pytest.raises(ValueError, match="Terminal-value improvement"):
        validate_v5_protocol(payload)


def test_experiment_record_requires_reason_chain() -> None:
    record = ExperimentRecord(
        experiment_id="V5-FUND-001",
        status="frozen_candidate",
        hypothesis="Rebalance family weights.",
        allowed_change="family weights only",
        expected_mechanism="Improve selection quality.",
        config_path="config/v5/exp001.json",
        config_sha256="abc",
        protocol_id="v5_evaluation_protocol_v1",
        protocol_sha256="def",
        dataset_sha256="ghi",
        code_commit="123",
    )
    record.validate()
