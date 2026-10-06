from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


FROZEN_PROTOCOL_ID = "v5_evaluation_protocol_v1"


def canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_v5_protocol(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_v5_protocol(payload)
    return payload


def validate_v5_protocol(payload: dict[str, Any]) -> None:
    if payload.get("protocol_id") != FROZEN_PROTOCOL_ID:
        raise ValueError("Unexpected V5 evaluation protocol id")
    if payload.get("status") != "frozen":
        raise ValueError("V5 evaluation protocol must be frozen")
    if payload.get("frozen_before_final_candidate_review") is not True:
        raise ValueError("Protocol must be frozen before final candidate review")

    objective = payload.get("primary_objective") or {}
    if objective.get("name") != "matched_cash_flow_terminal_value":
        raise ValueError("V5 primary objective must remain matched-cash-flow terminal value")

    economic = ((payload.get("hard_gates") or {}).get("economic") or {})
    if economic.get("require_both_terminal_value_and_xirr_gates") is not True:
        raise ValueError("Both V5 economic gates must be required")
    if float(economic.get("minimum_terminal_value_improvement_pct", 0)) <= 0:
        raise ValueError("Terminal-value improvement gate must be positive")
    if float(economic.get("minimum_xirr_delta", 0)) <= 0:
        raise ValueError("XIRR improvement gate must be positive")

    decision = payload.get("decision_rule") or {}
    if decision.get("historical_pass_authorizes_live_trading") is not False:
        raise ValueError("Historical V5 pass may not authorize live trading")
    if decision.get("explicit_human_promotion_decision_required") is not True:
        raise ValueError("Explicit human promotion decision must remain required")

    governance = payload.get("experiment_governance") or {}
    if governance.get("frozen_candidate_config_may_not_change_in_place") is not True:
        raise ValueError("Frozen V5 candidate configs must be immutable")
    if governance.get("rejected_and_negative_results_must_be_retained") is not True:
        raise ValueError("Negative V5 experiments must remain in the registry")
    if governance.get("unrestricted_grid_search_prohibited") is not True:
        raise ValueError("Unrestricted grid search must remain prohibited")


def assert_protocol_hash(
    path: str | Path,
    expected_hash: str,
) -> str:
    payload = load_v5_protocol(path)
    actual = canonical_json_sha256(payload)
    if actual != expected_hash:
        raise ValueError(
            "Frozen V5 protocol hash mismatch: "
            + actual
            + " != "
            + expected_hash
        )
    return actual


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    status: str
    hypothesis: str
    allowed_change: str
    expected_mechanism: str
    config_path: str
    config_sha256: str
    protocol_id: str
    protocol_sha256: str
    dataset_sha256: str
    code_commit: str
    observed_selection_effect: str = ""
    economic_result_reason: str = ""
    disposition: str = ""
    reject_or_advance_reason: str = ""
    supersedes: str = ""
    superseded_by: str = ""

    def validate(self) -> None:
        required = {
            "experiment_id": self.experiment_id,
            "status": self.status,
            "hypothesis": self.hypothesis,
            "allowed_change": self.allowed_change,
            "expected_mechanism": self.expected_mechanism,
            "config_path": self.config_path,
            "config_sha256": self.config_sha256,
            "protocol_id": self.protocol_id,
            "protocol_sha256": self.protocol_sha256,
            "dataset_sha256": self.dataset_sha256,
            "code_commit": self.code_commit,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError("Experiment record missing: " + ", ".join(sorted(missing)))
        if self.protocol_id != FROZEN_PROTOCOL_ID:
            raise ValueError("Experiment uses unexpected V5 protocol")
        if self.status == "frozen_candidate" and not self.config_sha256:
            raise ValueError("Frozen candidate requires config hash")
