import pandas as pd

from finance.research.missingness_bias import (
    cohort_counts,
    compare_variants,
    coverage_table,
    forward_return_analysis,
    prepare_missingness_panel,
    summarize_missingness_bias,
)


def _rows(*, valuation_for_b: bool, b_score: float = 70.0) -> pd.DataFrame:
    rows = []
    for week, day in enumerate(("2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23")):
        for ticker, score in (("A", 90.0), ("B", b_score), ("C", 60.0)):
            valuation = 50.0 if ticker != "B" or valuation_for_b else None
            families = 4 if valuation is not None else 3
            rows.append(
                {
                    "decision_date": day,
                    "ticker": ticker,
                    "quality_score": 50.0,
                    "financial_health_score": 50.0,
                    "growth_score": 50.0,
                    "valuation_score": valuation,
                    "long_growth_v1_score": score,
                    "long_growth_v1_eligible": True,
                    "top_conviction_eligible": families == 4,
                    "return_price": 100 + week * 10 + (0 if ticker == "A" else 1),
                    "market_cap": 5_000_000_000,
                }
            )
    return pd.DataFrame(rows)


def test_cohorts_and_coverage_keep_missingness_explicit() -> None:
    panel = _rows(valuation_for_b=False)
    prepared = prepare_missingness_panel(panel)
    b = prepared.loc[prepared["ticker"].eq("B")].iloc[0]

    assert b["family_cohort"] == "three_family"
    assert b["missing_families"] == "valuation"
    coverage = coverage_table(panel)
    valuation = coverage.loc[coverage["metric"].eq("family:valuation")].iloc[0]
    assert valuation["present"] == 8
    assert valuation["rows"] == 12
    cohorts = cohort_counts(panel)
    assert cohorts.loc[cohorts["family_cohort"].eq("three_family"), "rows"].sum() == 4


def test_forward_returns_use_start_date_cohort_and_bounded_horizon() -> None:
    panel = _rows(valuation_for_b=False)
    detail, summary = forward_return_analysis(panel, horizons=(1,))

    assert len(detail) == 9
    assert set(detail["day_gap"]) == {7}
    b = detail.loc[detail["ticker"].eq("B")]
    assert set(b["family_cohort"]) == {"three_family"}
    assert set(b["missing_families"]) == {"valuation"}
    assert summary["observations"].sum() == 9


def test_variant_comparison_separates_availability_from_score_only_change() -> None:
    baseline = _rows(valuation_for_b=False)
    challenger = _rows(valuation_for_b=True)
    challenger.loc[challenger["ticker"].eq("A"), "long_growth_v1_score"] += 1

    detail, weekly, turnover, ranks, concentration = compare_variants(
        baseline, challenger
    )

    b = detail.loc[detail["ticker"].eq("B")]
    a = detail.loc[detail["ticker"].eq("A")]
    assert set(b["effect_type"]) == {"availability_gain"}
    assert set(a["effect_type"]) == {"score_only_change"}
    assert weekly["overlap_count"].min() == 2
    assert set(turnover["variant"]) == {"baseline", "challenger"}
    assert not ranks.empty
    assert not concentration.empty


def test_string_false_flags_are_not_treated_as_true() -> None:
    frame = _rows(valuation_for_b=True).iloc[:3].copy()
    frame["top_conviction_eligible"] = ["True", "False", "False"]

    _, weekly, _, _, _ = compare_variants(frame, frame)

    assert weekly.loc[0, "baseline_count"] == 1


def test_summary_does_not_treat_empty_history_as_stable() -> None:
    baseline = _rows(valuation_for_b=False)
    challenger = _rows(valuation_for_b=True)
    for frame in (baseline, challenger):
        historical = ~frame["decision_date"].eq("2026-01-23")
        frame.loc[historical, "top_conviction_eligible"] = False

    forward, _ = forward_return_analysis(challenger, horizons=(1,))
    detail, weekly, turnover, ranks, concentration = compare_variants(
        baseline, challenger
    )
    summary = summarize_missingness_bias(
        baseline,
        challenger,
        detail,
        weekly,
        turnover,
        ranks,
        concentration,
        forward,
        point_in_time_violations=0,
        classification_metadata_available=False,
    )

    assert summary.populated_top10_comparison_dates == 0
    assert summary.comparable_turnover_transitions == 0
    assert summary.mean_replacement_rate_delta_percentage_points is None
    assert summary.max_weekly_replacement_rate_delta_percentage_points is None
    assert summary.max_market_cap_band_share_increase_percentage_points is None
    assert (
        summary.historical_top10_analysis_status
        == "not_evaluated_insufficient_populated_history"
    )
