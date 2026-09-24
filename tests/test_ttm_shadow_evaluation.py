from __future__ import annotations

import pandas as pd

from finance.research.ttm_shadow_evaluation import (
    build_period_summary,
    build_ticker_event_detail,
    render_evaluation_markdown,
    weekly_coverage,
)


def test_weekly_coverage_counts_family_and_conviction_availability() -> None:
    frame = pd.DataFrame(
        [
            {
                "quality_score": 80.0,
                "financial_health_score": 70.0,
                "growth_score": 60.0,
                "valuation_score": 50.0,
                "ttm_valuation_score": 55.0,
                "top_conviction_eligible": True,
                "v2_top_conviction_eligible": True,
            },
            {
                "quality_score": 75.0,
                "financial_health_score": None,
                "growth_score": 65.0,
                "valuation_score": None,
                "ttm_valuation_score": 45.0,
                "top_conviction_eligible": False,
                "v2_top_conviction_eligible": True,
            },
        ]
    )

    result = weekly_coverage(frame)

    assert result["universe_rows"] == 2
    assert result["quality_eligible"] == 2
    assert result["financial_health_eligible"] == 1
    assert result["growth_eligible"] == 2
    assert result["annual_valuation_eligible"] == 1
    assert result["ttm_valuation_eligible"] == 2
    assert result["v1_top_conviction_eligible"] == 1
    assert result["v2_top_conviction_eligible"] == 2


def test_period_summary_freezes_overlap_persistence_and_coverage_fields() -> None:
    ledger = pd.DataFrame(
        [
            {
                "as_of": "2026-09-25",
                "valid": True,
                "top10_overlap": 9,
                "entered_v2": "AAA",
                "exited_v2": "BBB",
                "pit_violations": 0,
                "v1_decision_hash": "v1-a",
                "v2_decision_hash": "v2-a",
                "input_bundle_sha256": "input-a",
                "code_commit": "commit-a",
            },
            {
                "as_of": "2026-10-02",
                "valid": True,
                "top10_overlap": 8,
                "entered_v2": "AAA|CCC",
                "exited_v2": "BBB|DDD",
                "pit_violations": 0,
                "v1_decision_hash": "v1-b",
                "v2_decision_hash": "v2-b",
                "input_bundle_sha256": "input-b",
                "code_commit": "commit-a",
            },
        ]
    )
    weekly = pd.DataFrame(
        [
            {
                "as_of": "2026-09-25",
                "artifact_consistent": True,
                "universe_rows": 501,
                "ttm_valuation_eligible": 435,
                "v2_top_conviction_eligible": 300,
            },
            {
                "as_of": "2026-10-02",
                "artifact_consistent": True,
                "universe_rows": 501,
                "ttm_valuation_eligible": 434,
                "v2_top_conviction_eligible": 301,
            },
        ]
    )
    events = pd.DataFrame(
        [
            {"as_of": "2026-09-25", "ticker": "AAA", "change": "entered_v2"},
            {"as_of": "2026-10-02", "ticker": "AAA", "change": "entered_v2"},
        ]
    )

    result = build_period_summary(ledger, weekly, events)

    assert result["evaluation_protocol"] == "issue24_shadow_evaluation_v1"
    assert result["valid_shadow_weeks"] == 2
    assert result["top10_overlap_mean"] == 8.5
    assert result["top10_overlap_median"] == 8.5
    assert result["top10_overlap_min"] == 8.0
    assert result["entered_v2_frequency"] == {"AAA": 2, "CCC": 1}
    assert result["exited_v2_frequency"] == {"BBB": 2, "DDD": 1}
    assert result["persistent_difference_tickers"] == ["AAA", "BBB"]
    assert result["weekly_artifact_consistency_failures"] == 0
    assert result["coverage_ranges"]["ttm_valuation_eligible"] == {
        "min": 434,
        "max": 435,
    }
    assert result["live_promotion_authorized"] is False


def test_ticker_event_detail_preserves_rank_evidence() -> None:
    comparison = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "change": "overlap",
                "v1_rank": 2,
                "v2_rank": 1,
                "rank_change_v2_minus_v1": -1,
            },
            {
                "ticker": "BBB",
                "change": "entered_v2",
                "v1_rank": None,
                "v2_rank": 10,
                "rank_change_v2_minus_v1": None,
            },
        ]
    )

    result = build_ticker_event_detail([("2026-09-25", comparison)])

    assert result["ticker"].tolist() == ["AAA", "BBB"]
    assert result.loc[0, "rank_change_v2_minus_v1"] == -1


def test_markdown_template_never_authorizes_live_promotion() -> None:
    summary = {
        "valid_shadow_weeks": 8,
        "recorded_ledger_rows": 8,
        "excluded_or_invalid_weeks_in_ledger": 0,
        "pit_violation_count": 0,
        "weekly_artifact_consistency_failures": 0,
        "top10_overlap_mean": 9.0,
        "top10_overlap_median": 9.0,
        "top10_overlap_min": 8.0,
        "persistent_difference_tickers": ["AAA"],
        "coverage_ranges": {},
        "unique_v1_decision_hashes": 8,
        "unique_v2_decision_hashes": 8,
        "unique_input_bundles": 8,
        "unique_code_commits": 1,
    }

    markdown = render_evaluation_markdown(summary, pd.DataFrame())

    assert "No automatic promotion" in markdown
    assert "separate explicit live-integration decision" in markdown
