from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from finance.data.sec_canonical import build_canonical_facts
from finance.research.ttm_sec_duration import build_ttm_duration_winners


def _sub(
    *,
    adsh: str,
    form: str,
    fp: str,
    fy: int,
    period: date,
    accepted: datetime,
) -> dict[str, object]:
    return {
        "adsh": adsh,
        "cik": 1,
        "name": "Example Co",
        "form": form,
        "fy": fy,
        "fp": fp,
        "period_date": period,
        "filed_date": accepted.date(),
        "accepted_at": accepted,
    }


def _num(
    *,
    adsh: str,
    tag: str,
    qtrs: int,
    value: float,
    ddate: date,
    uom: str = "USD",
    version: str = "us-gaap/2025",
) -> dict[str, object]:
    return {
        "adsh": adsh,
        "tag": tag,
        "version": version,
        "ddate_date": ddate,
        "qtrs": qtrs,
        "uom": uom,
        "value": value,
        "coreg": "",
        "segments": "",
    }


def _pre(*, adsh: str, tag: str, stmt: str) -> dict[str, object]:
    return {
        "adsh": adsh,
        "tag": tag,
        "version": "us-gaap/2025",
        "stmt": stmt,
    }


def test_v2_ttm_extractor_retains_income_ytd_while_v1_canonical_does_not() -> None:
    period = date(2025, 6, 30)
    submissions = pd.DataFrame([
        _sub(
            adsh="q2",
            form="10-Q",
            fp="Q2",
            fy=2025,
            period=period,
            accepted=datetime(2025, 8, 1, 10, 0),
        )
    ])
    numeric = pd.DataFrame([
        _num(
            adsh="q2",
            tag="Revenues",
            qtrs=1,
            value=130.0,
            ddate=period,
        ),
        _num(
            adsh="q2",
            tag="Revenues",
            qtrs=2,
            value=230.0,
            ddate=period,
        ),
        _num(
            adsh="q2",
            tag="NetIncomeLoss",
            qtrs=1,
            value=20.0,
            ddate=period,
        ),
        _num(
            adsh="q2",
            tag="NetIncomeLoss",
            qtrs=2,
            value=35.0,
            ddate=period,
        ),
    ])
    presentation = pd.DataFrame([
        _pre(adsh="q2", tag="Revenues", stmt="IS"),
        _pre(adsh="q2", tag="NetIncomeLoss", stmt="IS"),
    ])

    v1, _ = build_canonical_facts(
        submissions, numeric, presentation
    )
    v2, _, audit = build_ttm_duration_winners(
        submissions, numeric, presentation
    )

    assert sorted(v1["qtrs"].tolist()) == [1, 1]
    assert sorted(v2["qtrs"].tolist()) == [1, 1, 2, 2]
    assert audit.rows_output == 4
    assert audit.winner_unresolved_groups == 0


def test_v2_ttm_extractor_keeps_expected_cash_flow_ytd_and_annual_facts() -> None:
    q3_period = date(2025, 9, 30)
    fy_period = date(2025, 12, 31)
    submissions = pd.DataFrame([
        _sub(
            adsh="q3",
            form="10-Q",
            fp="Q3",
            fy=2025,
            period=q3_period,
            accepted=datetime(2025, 11, 1, 10, 0),
        ),
        _sub(
            adsh="fy",
            form="10-K",
            fp="FY",
            fy=2025,
            period=fy_period,
            accepted=datetime(2026, 2, 15, 10, 0),
        ),
    ])
    numeric = pd.DataFrame([
        _num(
            adsh="q3",
            tag="NetCashProvidedByUsedInOperatingActivities",
            qtrs=3,
            value=300.0,
            ddate=q3_period,
        ),
        _num(
            adsh="q3",
            tag="PaymentsToAcquirePropertyPlantAndEquipment",
            qtrs=3,
            value=60.0,
            ddate=q3_period,
        ),
        _num(
            adsh="fy",
            tag="NetCashProvidedByUsedInOperatingActivities",
            qtrs=4,
            value=430.0,
            ddate=fy_period,
        ),
        _num(
            adsh="fy",
            tag="PaymentsToAcquirePropertyPlantAndEquipment",
            qtrs=4,
            value=90.0,
            ddate=fy_period,
        ),
    ])
    presentation = pd.DataFrame([
        _pre(
            adsh="q3",
            tag="NetCashProvidedByUsedInOperatingActivities",
            stmt="CF",
        ),
        _pre(
            adsh="q3",
            tag="PaymentsToAcquirePropertyPlantAndEquipment",
            stmt="CF",
        ),
        _pre(
            adsh="fy",
            tag="NetCashProvidedByUsedInOperatingActivities",
            stmt="CF",
        ),
        _pre(
            adsh="fy",
            tag="PaymentsToAcquirePropertyPlantAndEquipment",
            stmt="CF",
        ),
    ])

    winners, _, audit = build_ttm_duration_winners(
        submissions, numeric, presentation
    )

    assert sorted(winners["qtrs"].tolist()) == [3, 3, 4, 4]
    assert audit.rows_output == 4


def test_v2_ttm_extractor_rejects_segment_and_wrong_statement_rows() -> None:
    period = date(2025, 9, 30)
    submissions = pd.DataFrame([
        _sub(
            adsh="q3",
            form="10-Q",
            fp="Q3",
            fy=2025,
            period=period,
            accepted=datetime(2025, 11, 1, 10, 0),
        )
    ])
    numeric = pd.DataFrame([
        {
            **_num(
                adsh="q3",
                tag="Revenues",
                qtrs=3,
                value=390.0,
                ddate=period,
            ),
            "segments": "segment-member",
        },
        _num(
            adsh="q3",
            tag="NetIncomeLoss",
            qtrs=3,
            value=55.0,
            ddate=period,
        ),
    ])
    presentation = pd.DataFrame([
        _pre(adsh="q3", tag="Revenues", stmt="IS"),
        _pre(adsh="q3", tag="NetIncomeLoss", stmt="BS"),
    ])

    winners, _, audit = build_ttm_duration_winners(
        submissions, numeric, presentation
    )

    assert winners.empty
    assert audit.rows_consolidated == 1
    assert audit.rows_statement_matched == 0


def test_v2_ttm_extractor_uses_existing_winner_resolution() -> None:
    period = date(2025, 6, 30)
    submissions = pd.DataFrame([
        _sub(
            adsh="q2",
            form="10-Q",
            fp="Q2",
            fy=2025,
            period=period,
            accepted=datetime(2025, 8, 1, 10, 0),
        )
    ])
    numeric = pd.DataFrame([
        _num(
            adsh="q2",
            tag="Revenues",
            qtrs=2,
            value=230.0,
            ddate=period,
        ),
        _num(
            adsh="q2",
            tag="SalesRevenueNet",
            qtrs=2,
            value=230.0,
            ddate=period,
        ),
    ])
    presentation = pd.DataFrame([
        _pre(adsh="q2", tag="Revenues", stmt="IS"),
        _pre(adsh="q2", tag="SalesRevenueNet", stmt="IS"),
    ])

    winners, winner_audit, audit = build_ttm_duration_winners(
        submissions, numeric, presentation
    )

    assert len(winners) == 1
    assert winners.iloc[0]["source_tag"] == "Revenues"
    assert len(winner_audit) == 1
    assert winner_audit.iloc[0]["resolution"] == "same_value"
    assert audit.winner_duplicate_groups == 1
    assert audit.winner_unresolved_groups == 0
