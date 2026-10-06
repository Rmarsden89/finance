from __future__ import annotations

import pandas as pd

from scripts.build_v5_attribution_coverage import (
    build_factor_coverage,
    build_family_coverage,
    build_horizon_coverage,
    build_rank_band_coverage,
)
from finance.factors.registry import FACTOR_REGISTRY
from finance.scoring.family_scores import FAMILY_DEFINITIONS


def fixture() -> pd.DataFrame:
    rows = []
    for ticker, band, mature in (
        ("AAA", "top10", True),
        ("BBB", "rank11_25", False),
    ):
        row = {
            "decision_date": "2026-01-02",
            "ticker": ticker,
            "v1_rank_band": band,
            "top_conviction_eligible": True,
            "long_growth_v1_score": 80.0,
            "v1_rank": 1 if ticker == "AAA" else 11,
            "fwd_1w_status": "mature" if mature else "pending",
        }
        for factor in FACTOR_REGISTRY:
            row[factor] = 1.0
            row[f"{factor}_validated"] = 1.0
            row[f"{factor}_score"] = 50.0
        for family in FAMILY_DEFINITIONS:
            row[f"{family}_score"] = 50.0
            row[f"{family}_eligible"] = True
            row[f"{family}_factor_count"] = 2
            row[f"{family}_weight_coverage"] = 1.0
        rows.append(row)
    return pd.DataFrame(rows)


def test_factor_and_family_coverage() -> None:
    frame = fixture()
    factors = build_factor_coverage(frame)
    families = build_family_coverage(frame)
    assert len(factors) == len(FACTOR_REGISTRY)
    assert factors["score_available_pct"].eq(1.0).all()
    assert len(families) == len(FAMILY_DEFINITIONS)
    assert families["eligible_pct"].eq(1.0).all()


def test_rank_band_coverage_retains_boundary_bands() -> None:
    result = build_rank_band_coverage(fixture())
    assert set(result["rank_band"]) == {"top10", "rank11_25"}
    assert result["rows"].sum() == 2


def test_horizon_coverage_separates_mature_and_pending() -> None:
    result = build_horizon_coverage(fixture(), (1,))
    all_row = result.loc[result["rank_band"] == "all"].iloc[0]
    assert all_row["rows"] == 2
    assert all_row["mature_rows"] == 1
    assert all_row["pending_rows"] == 1
    assert all_row["mature_pct"] == 0.5
