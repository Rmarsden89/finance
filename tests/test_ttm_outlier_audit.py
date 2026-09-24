from __future__ import annotations

import pandas as pd

from finance.research.ttm_outlier_audit import (
    current_missing_ttm_coverage,
    flag_annual_ttm_outliers,
    reconcile_q4_ttm_to_reported_annual,
    summarize_q4_reconciliation,
)


def test_flag_annual_ttm_outliers_flags_large_ratio_and_sign_change() -> None:
    frame = pd.DataFrame([
        {
            "ticker": "AAA",
            "cik": 1,
            "company_name": "AAA",
            "annual_revenue": 100.0,
            "ttm_revenue": 250.0,
            "annual_net_income": -10.0,
            "ttm_net_income": 20.0,
            "annual_operating_cash_flow": 50.0,
            "ttm_operating_cash_flow": 55.0,
            "annual_capital_expenditures": 10.0,
            "ttm_capital_expenditures": 11.0,
            "annual_free_cash_flow": 40.0,
            "ttm_free_cash_flow": 44.0,
        }
    ])

    result = flag_annual_ttm_outliers(frame)

    assert set(result["metric"]) == {"revenue", "net_income"}
    revenue = result.loc[result["metric"].eq("revenue")].iloc[0]
    assert "positive_ratio_outside_0.5x_2.0x" in revenue["flag_reasons"]
    net_income = result.loc[result["metric"].eq("net_income")].iloc[0]
    assert net_income["sign_change"]


def test_q4_reconciliation_identifies_exact_and_material_difference() -> None:
    ttm = pd.DataFrame([
        {
            "cik": 1,
            "concept": "revenue",
            "uom": "USD",
            "ttm_end_fy": 2025,
            "ttm_end_quarter": "Q4",
            "ttm_end_date": "2025-12-31",
            "ttm_value": 400.0,
        },
        {
            "cik": 2,
            "concept": "revenue",
            "uom": "USD",
            "ttm_end_fy": 2025,
            "ttm_end_quarter": "Q4",
            "ttm_end_date": "2025-12-31",
            "ttm_value": 500.0,
        },
    ])
    annual = pd.DataFrame([
        {
            "cik": 1,
            "concept": "revenue",
            "fy": 2025,
            "fp": "FY",
            "qtrs": 4,
            "uom": "USD",
            "value": 400.0,
            "accepted_at": "2026-02-15",
            "adsh": "a1",
            "source_tag": "Revenues",
        },
        {
            "cik": 2,
            "concept": "revenue",
            "fy": 2025,
            "fp": "FY",
            "qtrs": 4,
            "uom": "USD",
            "value": 400.0,
            "accepted_at": "2026-02-15",
            "adsh": "a2",
            "source_tag": "Revenues",
        },
    ])

    detail = reconcile_q4_ttm_to_reported_annual(
        ttm, annual, cutoff=pd.Timestamp("2026-09-15")
    )
    summary = summarize_q4_reconciliation(detail)

    assert int(detail["exact_within_numeric_tolerance"].sum()) == 1
    assert int(detail["material_difference_gt_1pct"].sum()) == 1
    assert int(summary.iloc[0]["q4_ttm_rows"]) == 2


def test_current_missing_ttm_coverage_explains_history_and_fcf() -> None:
    snapshot = pd.DataFrame([
        {"ticker": "AAA", "cik": 1, "company_name": "AAA"},
        {"ticker": "BBB", "cik": 2, "company_name": "BBB"},
    ])
    numerators = pd.DataFrame([
        {
            "ticker": "AAA", "cik": 1, "company_name": "AAA",
            "ttm_revenue": 100.0, "ttm_net_income": 10.0,
            "ttm_operating_cash_flow": 20.0,
            "ttm_capital_expenditures": None,
            "ttm_free_cash_flow": None,
        },
        {
            "ticker": "BBB", "cik": 2, "company_name": "BBB",
            "ttm_revenue": None, "ttm_net_income": None,
            "ttm_operating_cash_flow": None,
            "ttm_capital_expenditures": None,
            "ttm_free_cash_flow": None,
        },
    ])
    quarters = pd.DataFrame([
        {"cik": 2, "concept": "revenue"},
        {"cik": 2, "concept": "revenue"},
    ])
    audit = pd.DataFrame([
        {
            "cik": 2, "concept": "revenue",
            "ttm_end_date": "2026-03-31",
            "reason": "insufficient_prior_quarters",
        }
    ])

    result = current_missing_ttm_coverage(
        snapshot, numerators, quarters, audit
    )

    bbb_revenue = result.loc[
        result["ticker"].eq("BBB") & result["metric"].eq("revenue")
    ].iloc[0]
    assert bbb_revenue["missing_reason"] == "fewer_than_four_discrete_quarters"

    aaa_fcf = result.loc[
        result["ticker"].eq("AAA") & result["metric"].eq("free_cash_flow")
    ].iloc[0]
    assert aaa_fcf["missing_reason"] == "missing_ttm_capex"
