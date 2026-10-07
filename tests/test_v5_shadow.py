from pathlib import Path

import pandas as pd

from scripts.run_v5_shadow import CAPABILITIES, build_decision, score


def frozen_config() -> dict:
    return {
        "base_family_weights": {
            "quality": 0.35,
            "financial_health": 0.20,
            "growth": 0.35,
            "valuation": 0.10,
        },
        "growth_valuation_modifier": {
            "neutral_score": 50.0,
            "maximum_growth_to_valuation_shift": 0.10,
        },
        "final_combination": {
            "fundamental_candidate_weight": 0.95,
            "momentum_weight": 0.05,
        },
        "eligibility": {
            "minimum_fundamental_families": 3,
        },
    }


def test_v5_shadow_score_matches_frozen_rule() -> None:
    frame = pd.DataFrame([{
        "ticker": "AAA",
        "quality_score": 80.0,
        "financial_health_score": 60.0,
        "growth_score": 75.0,
        "valuation_score": 25.0,
        "momentum_score": 90.0,
    }])
    result = score(frame, frozen_config())
    fundamental = 80*.35 + 60*.20 + 75*.325 + 25*.125
    expected = .95*fundamental + .05*90
    assert abs(result.iloc[0]["v5_score"] - expected) < 1e-12
    assert bool(result.iloc[0]["v5_top_conviction_eligible"])


def test_v5_shadow_decision_is_execution_inert() -> None:
    rows = []
    for i in range(10):
        rows.append({
            "ticker": f"T{i:02d}",
            "quality_score": 80.0,
            "financial_health_score": 70.0,
            "growth_score": 60.0 + i,
            "valuation_score": 40.0,
            "momentum_score": 50.0,
        })
    scored = score(pd.DataFrame(rows), frozen_config())
    decision = build_decision(
        scored,
        as_of="2026-10-07",
        v1_decision_hash="v1-hash",
        input_bundle_sha256="input-hash",
        config_sha256="config-hash",
    )
    assert len(decision["top10"]) == 10
    assert decision["execution_capabilities"] == CAPABILITIES
    assert not any(decision["execution_capabilities"].values())


def test_v5_shadow_source_has_no_broker_or_order_imports() -> None:
    source = Path("scripts/run_v5_shadow.py").read_text(encoding="utf-8")
    forbidden = (
        "robinhood",
        "build_order_intents",
        "run_v1_submit",
        "order_review",
        "place_order",
        "cancel_order",
        "modify_order",
    )
    lowered = source.lower()
    # "order_review" appears only in the explicit false capability key.
    lowered = lowered.replace('"order_review": false', "")
    assert all(token not in lowered for token in forbidden)
