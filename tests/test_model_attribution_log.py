from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_model_attribution_log.py"
SPEC = importlib.util.spec_from_file_location("build_model_attribution_log", SCRIPT)
attrib = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(attrib)


def test_v2_classifies_eligibility_gain_and_rank_displacement() -> None:
    reason, _ = attrib.classify_v2_change(
        "AAA",
        direction="entered",
        score_row={
            "top_conviction_eligible": False,
            "v2_top_conviction_eligible": True,
        },
    )
    assert reason == "ttm_eligibility_gain"

    reason, _ = attrib.classify_v2_change(
        "BBB",
        direction="exited",
        score_row={
            "top_conviction_eligible": True,
            "v2_top_conviction_eligible": True,
        },
    )
    assert reason == "ttm_score_rank_displacement"


def test_v3_classifies_direct_recovery_and_indirect_displacement() -> None:
    recovery = {
        "AAA": {
            "shares_recovered": True,
            "liabilities_recovered": False,
        }
    }
    reason, _ = attrib.classify_v3_change(
        "AAA",
        direction="entered",
        recovery=recovery,
        direct_changed_entrants={"AAA"},
    )
    assert reason == "direct_v3_data_recovery"

    reason, _ = attrib.classify_v3_change(
        "BBB",
        direction="exited",
        recovery=recovery,
        direct_changed_entrants={"AAA"},
    )
    assert reason == "indirect_displacement_from_v3_recovery"


def test_v3_does_not_overclaim_causality_without_direct_recovery() -> None:
    reason, _ = attrib.classify_v3_change(
        "CCC",
        direction="entered",
        recovery={},
        direct_changed_entrants=set(),
    )
    assert reason == "indirect_v3_rerank"
