from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TtmChallengerDefinition:
    model_id: str = "long_growth_v2_ttm_valuation_v1"
    evaluation_start: date = date(2016, 1, 1)
    family_weights: tuple[tuple[str, float], ...] = (
        ("quality", 0.35),
        ("financial_health", 0.20),
        ("growth", 0.25),
        ("valuation", 0.20),
    )
    valuation_weights: tuple[tuple[str, float], ...] = (
        ("earnings_yield_ttm", 0.30),
        ("sales_yield_ttm", 0.20),
        ("free_cash_flow_yield_ttm", 0.30),
        ("book_to_market", 0.20),
    )
    valuation_minimum_factors: int = 2
    minimum_families: int = 3
    top_n: int = 10
    weekly_contribution: float = 10.0
    max_addon_position_weight: float = 0.10
    discretionary_selling: bool = False


@dataclass(frozen=True)
class TtmPromotionThresholds:
    # Safety / data integrity.
    max_pit_violations: int = 0
    require_v1_regression_unchanged: bool = True
    minimum_overall_valuation_coverage_ratio_vs_v1: float = 0.95
    minimum_calendar_year_valuation_coverage_ratio_vs_v1: float = 0.90

    # Structural continuity of the valuation challenger.
    minimum_mean_weekly_valuation_spearman: float = 0.85
    minimum_median_weekly_valuation_top10_overlap: int = 6
    maximum_median_weekly_valuation_rank_shift: float = 25.0

    # Full-model continuity.
    minimum_median_weekly_full_model_top10_overlap: int = 7
    maximum_mean_top10_replacement_rate_increase: float = 0.025

    # Performance / robustness gates versus the frozen V1 champion.
    minimum_full_period_xirr_delta: float = 0.0
    maximum_full_period_drawdown_increase: float = 0.02
    minimum_rolling_xirr_win_rate: float = 0.50
    minimum_rolling_median_xirr_delta: float = 0.0
    minimum_benchmark_beating_window_count_delta: int = 0

    # Prospective governance.
    minimum_shadow_weeks: int = 8
    require_explicit_live_authorization: bool = True


TTM_CHALLENGER = TtmChallengerDefinition()
TTM_PROMOTION_THRESHOLDS = TtmPromotionThresholds()


ROLLING_WINDOW_YEARS = (3, 5)
