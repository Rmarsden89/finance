from __future__ import annotations

import pandas as pd

from finance.research.ttm_valuation import reconstruct_discrete_quarters_as_of


def _row(
    *,
    value: float,
    fp: str,
    qtrs: int,
    ddate: str,
    accepted: str,
    adsh: str,
    fy: int = 2025,
    form: str = "10-Q",
    cik: int = 1,
    concept: str = "revenue",
    uom: str = "USD",
    source_tag: str = "Revenues",
) -> dict[str, object]:
    return {
        "cik": cik,
        "concept": concept,
        "value": value,
        "uom": uom,
        "fy": fy,
        "fp": fp,
        "qtrs": qtrs,
        "ddate_date": ddate,
        "accepted_at": accepted,
        "adsh": adsh,
        "source_tag": source_tag,
        "form": form,
    }


def test_reconstructs_q1_q2_q3_and_q4_from_ytd_duration_facts() -> None:
    facts = pd.DataFrame([
        _row(
            value=100, fp="Q1", qtrs=1, ddate="2025-03-31",
            accepted="2025-05-01 10:00:00", adsh="q1",
        ),
        _row(
            value=230, fp="Q2", qtrs=2, ddate="2025-06-30",
            accepted="2025-08-01 10:00:00", adsh="q2",
        ),
        _row(
            value=390, fp="Q3", qtrs=3, ddate="2025-09-30",
            accepted="2025-11-01 10:00:00", adsh="q3",
        ),
        _row(
            value=560, fp="FY", qtrs=4, ddate="2025-12-31",
            accepted="2026-02-15 10:00:00", adsh="fy", form="10-K",
        ),
    ])

    result = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2026-03-01")
    )
    values = result.quarters.set_index("fiscal_quarter")["value"].to_dict()
    derivations = (
        result.quarters.set_index("fiscal_quarter")["derivation"].to_dict()
    )

    assert values == {"Q1": 100.0, "Q2": 130.0, "Q3": 160.0, "Q4": 170.0}
    assert derivations == {
        "Q1": "direct_qtrs_1",
        "Q2": "q2_ytd_minus_q1",
        "Q3": "q3_ytd_minus_q2_ytd",
        "Q4": "annual_minus_q3_ytd",
    }


def test_prefers_direct_q2_and_q3_when_available() -> None:
    facts = pd.DataFrame([
        _row(
            value=100, fp="Q1", qtrs=1, ddate="2025-03-31",
            accepted="2025-05-01", adsh="q1",
        ),
        _row(
            value=230, fp="Q2", qtrs=2, ddate="2025-06-30",
            accepted="2025-08-01", adsh="q2ytd",
        ),
        _row(
            value=135, fp="Q2", qtrs=1, ddate="2025-06-30",
            accepted="2025-08-01", adsh="q2direct",
        ),
        _row(
            value=390, fp="Q3", qtrs=3, ddate="2025-09-30",
            accepted="2025-11-01", adsh="q3ytd",
        ),
        _row(
            value=165, fp="Q3", qtrs=1, ddate="2025-09-30",
            accepted="2025-11-01", adsh="q3direct",
        ),
        _row(
            value=560, fp="FY", qtrs=4, ddate="2025-12-31",
            accepted="2026-02-15", adsh="fy", form="10-K",
        ),
    ])

    result = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2026-03-01")
    )
    quarter = result.quarters.set_index("fiscal_quarter")

    assert quarter.loc["Q2", "value"] == 135.0
    assert quarter.loc["Q2", "derivation"] == "direct_qtrs_1"
    assert quarter.loc["Q3", "value"] == 165.0
    assert quarter.loc["Q3", "derivation"] == "direct_qtrs_1"
    assert quarter.loc["Q4", "value"] == 170.0


def test_amendment_replaces_prior_ytd_only_after_acceptance() -> None:
    facts = pd.DataFrame([
        _row(
            value=100, fp="Q1", qtrs=1, ddate="2025-03-31",
            accepted="2025-05-01", adsh="q1",
        ),
        _row(
            value=230, fp="Q2", qtrs=2, ddate="2025-06-30",
            accepted="2025-08-01", adsh="q2-original",
        ),
        _row(
            value=240, fp="Q2", qtrs=2, ddate="2025-06-30",
            accepted="2025-08-20", adsh="q2-amended", form="10-Q/A",
        ),
    ])

    before = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2025-08-10")
    ).quarters.set_index("fiscal_quarter")
    after = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2025-08-21")
    ).quarters.set_index("fiscal_quarter")

    assert before.loc["Q2", "value"] == 130.0
    assert before.loc["Q2", "source_adshs"] == "q1|q2-original"
    assert after.loc["Q2", "value"] == 140.0
    assert after.loc["Q2", "source_adshs"] == "q1|q2-amended"


def test_non_calendar_fiscal_year_uses_fy_and_period_order() -> None:
    facts = pd.DataFrame([
        _row(
            value=50, fp="Q1", qtrs=1, ddate="2024-10-31",
            accepted="2024-12-01", adsh="q1", fy=2025,
        ),
        _row(
            value=125, fp="Q2", qtrs=2, ddate="2025-01-31",
            accepted="2025-03-01", adsh="q2", fy=2025,
        ),
        _row(
            value=225, fp="Q3", qtrs=3, ddate="2025-04-30",
            accepted="2025-06-01", adsh="q3", fy=2025,
        ),
        _row(
            value=350, fp="FY", qtrs=4, ddate="2025-07-31",
            accepted="2025-09-01", adsh="fy", fy=2025, form="10-K",
        ),
    ])

    result = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2025-09-15")
    )
    values = result.quarters.set_index("fiscal_quarter")["value"].to_dict()

    assert values == {"Q1": 50.0, "Q2": 75.0, "Q3": 100.0, "Q4": 125.0}


def test_does_not_mix_units_when_deriving_quarters() -> None:
    facts = pd.DataFrame([
        _row(
            value=100, fp="Q1", qtrs=1, ddate="2025-03-31",
            accepted="2025-05-01", adsh="q1-usd", uom="USD",
        ),
        _row(
            value=230, fp="Q2", qtrs=2, ddate="2025-06-30",
            accepted="2025-08-01", adsh="q2-eur", uom="EUR",
        ),
    ])

    result = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2025-09-01")
    )

    assert set(result.quarters["fiscal_quarter"]) == {"Q1"}
    reasons = set(result.audit["reason"])
    assert "missing_q2_ytd" in reasons
    assert "missing_q1" in reasons


def test_missing_inputs_are_audited_not_imputed() -> None:
    facts = pd.DataFrame([
        _row(
            value=100, fp="Q1", qtrs=1, ddate="2025-03-31",
            accepted="2025-05-01", adsh="q1",
        ),
        _row(
            value=390, fp="Q3", qtrs=3, ddate="2025-09-30",
            accepted="2025-11-01", adsh="q3",
        ),
        _row(
            value=560, fp="FY", qtrs=4, ddate="2025-12-31",
            accepted="2026-02-15", adsh="fy", form="10-K",
        ),
    ])

    result = reconstruct_discrete_quarters_as_of(
        facts, as_of=pd.Timestamp("2026-03-01")
    )

    reasons = {
        (row.fiscal_quarter, row.reason)
        for row in result.audit.itertuples()
    }
    assert ("Q2", "missing_q2_ytd") in reasons
    assert ("Q3", "missing_q2_ytd") in reasons
    assert "Q2" not in set(result.quarters["fiscal_quarter"])
    assert "Q3" not in set(result.quarters["fiscal_quarter"])
    assert result.quarters.set_index("fiscal_quarter").loc["Q4", "value"] == 170.0
