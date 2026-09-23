from __future__ import annotations

import pandas as pd
import pytest

from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.ttm_shadow import (
    append_shadow_ledger,
    build_top10_comparison,
    build_v2_decision_payload,
)


def _v2_current() -> pd.DataFrame:
    rows = []
    score_column = f"{TTM_CHALLENGER.model_id}_score"
    for rank in range(1, 12):
        rows.append({
            "ticker": f"T{rank:02d}",
            score_column: float(100 - rank),
            "v2_top_conviction_eligible": True,
        })
    return pd.DataFrame(rows)


def _v1_decision() -> dict:
    return {
        "decision_hash": "v1hash",
        "decisions": [
            {
                "rank": rank,
                "ticker": f"T{rank:02d}",
                "score": float(100 - rank),
            }
            for rank in range(2, 12)
        ],
    }


def test_shadow_top10_comparison_tracks_one_entry_and_exit() -> None:
    comparison = build_top10_comparison(
        v1_decision=_v1_decision(),
        v2_current=_v2_current(),
    )

    assert int(comparison["in_both"].sum()) == 9
    assert comparison.loc[
        comparison["change"].eq("entered_v2"), "ticker"
    ].tolist() == ["T01"]
    assert comparison.loc[
        comparison["change"].eq("exited_v2"), "ticker"
    ].tolist() == ["T11"]


def test_v2_shadow_decision_hash_is_deterministic() -> None:
    left = build_v2_decision_payload(
        as_of="2026-09-30",
        v1_decision_hash="v1hash",
        v2_current=_v2_current(),
        input_bundle_sha256="inputs",
    )
    right = build_v2_decision_payload(
        as_of="2026-09-30",
        v1_decision_hash="v1hash",
        v2_current=_v2_current(),
        input_bundle_sha256="inputs",
    )

    assert left["decision_hash"] == right["decision_hash"]
    assert len(left["top10"]) == 10
    assert left["top10"][0]["ticker"] == "T01"
    assert not any(left["execution_capabilities"].values())


def test_v2_shadow_decision_hash_changes_with_inputs() -> None:
    left = build_v2_decision_payload(
        as_of="2026-09-30",
        v1_decision_hash="v1hash",
        v2_current=_v2_current(),
        input_bundle_sha256="inputs-a",
    )
    right = build_v2_decision_payload(
        as_of="2026-09-30",
        v1_decision_hash="v1hash",
        v2_current=_v2_current(),
        input_bundle_sha256="inputs-b",
    )

    assert left["decision_hash"] != right["decision_hash"]


def test_shadow_ledger_rejects_duplicate_week() -> None:
    ledger = pd.DataFrame([
        {
            "as_of": "2026-09-30",
            "v2_decision_hash": "first",
            "valid": True,
        }
    ])

    with pytest.raises(ValueError, match="already contains observation"):
        append_shadow_ledger(
            ledger,
            {
                "as_of": "2026-09-30",
                "v2_decision_hash": "second",
                "valid": True,
            },
        )


def test_shadow_ledger_appends_new_week_in_date_order() -> None:
    ledger = pd.DataFrame([
        {
            "as_of": "2026-10-07",
            "v2_decision_hash": "later",
            "valid": True,
        }
    ])

    result = append_shadow_ledger(
        ledger,
        {
            "as_of": "2026-09-30",
            "v2_decision_hash": "earlier",
            "valid": True,
        },
    )

    assert result["as_of"].tolist() == ["2026-09-30", "2026-10-07"]
