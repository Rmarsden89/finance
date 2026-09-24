from __future__ import annotations

from finance.research.ttm_promotion_criteria import (
    ROLLING_WINDOW_YEARS,
    TTM_CHALLENGER,
    TTM_PROMOTION_THRESHOLDS,
)


def test_ttm_challenger_changes_only_valuation_inputs() -> None:
    assert TTM_CHALLENGER.model_id == "long_growth_v2_ttm_valuation_v1"
    assert TTM_CHALLENGER.evaluation_start.isoformat() == "2016-01-01"
    assert dict(TTM_CHALLENGER.family_weights) == {
        "quality": 0.35,
        "financial_health": 0.20,
        "growth": 0.25,
        "valuation": 0.20,
    }
    assert dict(TTM_CHALLENGER.valuation_weights) == {
        "earnings_yield_ttm": 0.30,
        "sales_yield_ttm": 0.20,
        "free_cash_flow_yield_ttm": 0.30,
        "book_to_market": 0.20,
    }
    assert TTM_CHALLENGER.valuation_minimum_factors == 2
    assert TTM_CHALLENGER.minimum_families == 3
    assert TTM_CHALLENGER.top_n == 10
    assert TTM_CHALLENGER.weekly_contribution == 10.0
    assert TTM_CHALLENGER.max_addon_position_weight == 0.10
    assert not TTM_CHALLENGER.discretionary_selling


def test_ttm_promotion_thresholds_are_frozen_and_multidimensional() -> None:
    thresholds = TTM_PROMOTION_THRESHOLDS

    assert thresholds.max_pit_violations == 0
    assert thresholds.require_v1_regression_unchanged
    assert thresholds.minimum_overall_valuation_coverage_ratio_vs_v1 == 0.95
    assert thresholds.minimum_calendar_year_valuation_coverage_ratio_vs_v1 == 0.90
    assert thresholds.minimum_mean_weekly_valuation_spearman == 0.85
    assert thresholds.minimum_median_weekly_valuation_top10_overlap == 6
    assert thresholds.maximum_median_weekly_valuation_rank_shift == 25.0
    assert thresholds.minimum_median_weekly_full_model_top10_overlap == 7
    assert thresholds.maximum_mean_top10_replacement_rate_increase == 0.025
    assert thresholds.minimum_full_period_xirr_delta == 0.0
    assert thresholds.maximum_full_period_drawdown_increase == 0.02
    assert thresholds.minimum_rolling_xirr_win_rate == 0.50
    assert thresholds.minimum_rolling_median_xirr_delta == 0.0
    assert thresholds.minimum_benchmark_beating_window_count_delta == 0
    assert thresholds.minimum_shadow_weeks == 8
    assert thresholds.require_explicit_live_authorization
    assert ROLLING_WINDOW_YEARS == (3, 5)
