from scripts.build_v2_historical_ttm_valuation_replay import _merge_frozen_book_scores
from __future__ import annotations

import pandas as pd

from finance.research.ttm_historical_replay import (
    audit_historical_ttm_pit,
    build_quarter_events,
    build_ttm_events,
    replay_ttm_numerators_to_panel,
)
from finance.research.ttm_valuation import (
    build_ttm_values,
    reconstruct_discrete_quarters_as_of,
)


def _fact(
    *,
    fy: int,
    fp: str,
    qtrs: int,
    value: float,
    ddate: str,
    accepted: str,
    adsh: str,
    concept: str = "revenue",
    cik: int = 1,
    uom: str = "USD",
) -> dict[str, object]:
    return {
        "cik": cik,
        "concept": concept,
        "fy": fy,
        "fp": fp,
        "qtrs": qtrs,
        "value": value,
        "ddate_date": ddate,
        "accepted_at": accepted,
        "adsh": adsh,
        "source_tag": "Revenues" if concept == "revenue" else concept,
        "form": "10-K/A" if fp == "FY" and adsh.endswith("a") else (
            "10-K" if fp == "FY" else "10-Q"
        ),
        "uom": uom,
    }


def _reference_latest(
    facts: pd.DataFrame,
    cutoff: str,
    concept: str = "revenue",
) -> float:
    quarters = reconstruct_discrete_quarters_as_of(
        facts,
        as_of=pd.Timestamp(cutoff),
        income_quarter_policy="ytd_preferred",
    ).quarters
    values = build_ttm_values(
        quarters,
        as_of=pd.Timestamp(cutoff),
    ).values
    values = values.loc[values["concept"].eq(concept)].sort_values(
        ["ttm_end_date", "available_at"], kind="stable"
    )
    return float(values.iloc[-1]["ttm_value"])


def test_historical_event_replay_matches_reference_across_amendment() -> None:
    facts = pd.DataFrame([
        _fact(
            fy=2024, fp="Q1", qtrs=1, value=10,
            ddate="2024-03-31", accepted="2024-05-01", adsh="24q1",
        ),
        _fact(
            fy=2024, fp="Q2", qtrs=2, value=30,
            ddate="2024-06-30", accepted="2024-08-01", adsh="24q2",
        ),
        _fact(
            fy=2024, fp="Q3", qtrs=3, value=60,
            ddate="2024-09-30", accepted="2024-11-01", adsh="24q3",
        ),
        _fact(
            fy=2024, fp="FY", qtrs=4, value=100,
            ddate="2024-12-31", accepted="2025-02-15", adsh="24fy",
        ),
        _fact(
            fy=2025, fp="Q1", qtrs=1, value=50,
            ddate="2025-03-31", accepted="2025-05-01", adsh="25q1",
        ),
        _fact(
            fy=2024, fp="FY", qtrs=4, value=110,
            ddate="2024-12-31", accepted="2025-06-01", adsh="24fya",
        ),
    ])

    quarter_events = build_quarter_events(facts)
    ttm_events = build_ttm_events(quarter_events)

    panel = pd.DataFrame([
        {
            "decision_date": "2025-05-02",
            "as_of": "2025-05-02 16:00:00",
            "ticker": "AAA",
            "cik": 1,
        },
        {
            "decision_date": "2025-06-06",
            "as_of": "2025-06-06 16:00:00",
            "ticker": "AAA",
            "cik": 1,
        },
    ])
    replay = replay_ttm_numerators_to_panel(panel, ttm_events)

    before = _reference_latest(facts, "2025-05-02 16:00:00")
    after = _reference_latest(facts, "2025-06-06 16:00:00")

    assert before == 140.0
    assert after == 150.0
    assert replay.loc[
        replay["decision_date"].eq("2025-05-02"), "ttm_revenue"
    ].iloc[0] == before
    assert replay.loc[
        replay["decision_date"].eq("2025-06-06"), "ttm_revenue"
    ].iloc[0] == after
    assert audit_historical_ttm_pit(replay).empty


def test_historical_replay_selects_latest_common_cash_flow_endpoint() -> None:
    facts: list[dict[str, object]] = []
    for concept, multiplier in (
        ("operating_cash_flow", 10.0),
        ("capital_expenditures", 2.0),
    ):
        facts.extend([
            _fact(
                fy=2024, fp="Q1", qtrs=1, value=1 * multiplier,
                ddate="2024-03-31", accepted="2024-05-01",
                adsh=f"{concept}24q1", concept=concept,
            ),
            _fact(
                fy=2024, fp="Q2", qtrs=2, value=3 * multiplier,
                ddate="2024-06-30", accepted="2024-08-01",
                adsh=f"{concept}24q2", concept=concept,
            ),
            _fact(
                fy=2024, fp="Q3", qtrs=3, value=6 * multiplier,
                ddate="2024-09-30", accepted="2024-11-01",
                adsh=f"{concept}24q3", concept=concept,
            ),
            _fact(
                fy=2024, fp="FY", qtrs=4, value=10 * multiplier,
                ddate="2024-12-31", accepted="2025-02-15",
                adsh=f"{concept}24fy", concept=concept,
            ),
        ])

    quarter_events = build_quarter_events(pd.DataFrame(facts))
    ttm_events = build_ttm_events(quarter_events)
    panel = pd.DataFrame([{
        "decision_date": "2025-02-21",
        "as_of": "2025-02-21 16:00:00",
        "ticker": "AAA",
        "cik": 1,
    }])

    replay = replay_ttm_numerators_to_panel(panel, ttm_events)

    assert replay.loc[0, "ttm_operating_cash_flow"] == 100.0
    assert replay.loc[0, "ttm_capital_expenditures"] == 20.0
    assert replay.loc[0, "ttm_free_cash_flow"] == 80.0


def test_historical_pit_audit_handles_missing_and_future_availability() -> None:
    replay = pd.DataFrame([
        {
            "decision_date": "2025-05-02",
            "as_of": "2025-05-02 16:00:00",
            "ticker": "AAA",
            "cik": 1,
            "ttm_revenue_available_at": pd.NaT,
            "ttm_net_income_available_at": "2025-05-02 15:00:00",
            "ttm_cash_flow_available_at": pd.NaT,
        },
        {
            "decision_date": "2025-05-02",
            "as_of": "2025-05-02 16:00:00",
            "ticker": "BBB",
            "cik": 2,
            "ttm_revenue_available_at": "2025-05-02 17:00:00",
            "ttm_net_income_available_at": pd.NaT,
            "ttm_cash_flow_available_at": pd.NaT,
        },
    ])

    audit = audit_historical_ttm_pit(replay)

    assert len(audit) == 1
    assert audit.iloc[0]["ticker"] == "BBB"
    assert audit.iloc[0]["field"] == "ttm_revenue_available_at"
    assert audit.iloc[0]["status"] == "available_after_cutoff"


def test_historical_event_builder_ignores_missing_fiscal_year_rows() -> None:
    facts = pd.DataFrame([
        _fact(
            fy=2024, fp="Q1", qtrs=1, value=10,
            ddate="2024-03-31", accepted="2024-05-01", adsh="q1",
        ),
        _fact(
            fy=2024, fp="Q2", qtrs=2, value=30,
            ddate="2024-06-30", accepted="2024-08-01", adsh="q2",
        ),
        _fact(
            fy=2024, fp="Q3", qtrs=3, value=60,
            ddate="2024-09-30", accepted="2024-11-01", adsh="q3",
        ),
        _fact(
            fy=2024, fp="FY", qtrs=4, value=100,
            ddate="2024-12-31", accepted="2025-02-15", adsh="fy",
        ),
        {
            **_fact(
                fy=2024, fp="Q1", qtrs=1, value=999,
                ddate="2024-03-31", accepted="2024-05-02", adsh="bad",
            ),
            "fy": pd.NA,
        },
    ])

    quarter_events = build_quarter_events(facts)
    ttm_events = build_ttm_events(quarter_events)

    assert not quarter_events.empty
    assert quarter_events["fy"].notna().all()
    assert len(ttm_events) == 1
    assert ttm_events.iloc[0]["ttm_value"] == 100.0


def test_historical_book_score_merge_normalizes_decision_date_dtype() -> None:
    result = pd.DataFrame([
        {
            "decision_date": "2025-06-06",
            "ticker": "AAA",
            "book_to_market": 0.1,
        }
    ])
    annual = pd.DataFrame([
        {
            "decision_date": pd.Timestamp("2025-06-06"),
            "ticker": "AAA",
            "book_to_market": 0.5,
            "book_to_market_valid": True,
            "book_to_market_invalid_reason": "",
            "book_to_market_validated": 0.5,
            "book_to_market_winsorized": 0.5,
            "book_to_market_winsorized_flag": False,
            "book_to_market_percentile": 0.75,
            "book_to_market_score": 75.0,
        }
    ])

    merged = _merge_frozen_book_scores(result, annual)

    assert pd.api.types.is_datetime64_ns_dtype(merged["decision_date"])
    assert merged.loc[0, "book_to_market"] == 0.5
    assert merged.loc[0, "book_to_market_score"] == 75.0
