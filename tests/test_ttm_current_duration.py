from __future__ import annotations

from datetime import date

from finance.research.ttm_current_duration import (
    extract_current_ttm_duration_candidates,
)


def _payload() -> dict:
    return {
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 200.0,
                            },
                            {
                                "accn": "0001",
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 105.0,
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 20.0,
                            },
                            {
                                "accn": "0001",
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 11.0,
                            },
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 30.0,
                            }
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "val": 5.0,
                            }
                        ]
                    }
                },
            }
        },
    }


def test_current_ttm_extractor_retains_income_direct_and_ytd_q2() -> None:
    frame, audit = extract_current_ttm_duration_candidates(
        _payload(),
        ticker="AAA",
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 8, 1),
        accepted_at="2026-08-01T12:00:00-04:00",
    )

    revenue = frame.loc[frame["concept"].eq("revenue")]
    net_income = frame.loc[frame["concept"].eq("net_income")]

    assert set(revenue["qtrs"]) == {1, 2}
    assert set(net_income["qtrs"]) == {1, 2}
    assert audit.rows_output >= 4
    assert set(frame["source_system"]) == {"sec_companyfacts_current_ttm"}


def test_current_ttm_extractor_retains_cash_flow_ytd_only() -> None:
    frame, _ = extract_current_ttm_duration_candidates(
        _payload(),
        ticker="AAA",
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 8, 1),
        accepted_at="2026-08-01T12:00:00-04:00",
    )

    cash = frame.loc[
        frame["concept"].isin(
            {"operating_cash_flow", "capital_expenditures"}
        )
    ]
    assert not cash.empty
    assert set(cash["qtrs"]) == {2}


def test_current_ttm_extractor_rejects_other_accessions_and_periods() -> None:
    payload = _payload()
    payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"].extend([
        {
            "accn": "other",
            "start": "2026-01-01",
            "end": "2026-06-30",
            "form": "10-Q",
            "fy": 2026,
            "fp": "Q2",
            "val": 999.0,
        },
        {
            "accn": "0001",
            "start": "2025-10-01",
            "end": "2026-03-31",
            "form": "10-Q",
            "fy": 2026,
            "fp": "Q1",
            "val": 999.0,
        },
    ])

    frame, _ = extract_current_ttm_duration_candidates(
        payload,
        ticker="AAA",
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 8, 1),
        accepted_at="2026-08-01T12:00:00-04:00",
    )

    assert 999.0 not in set(frame["value"])


def test_current_ttm_extractor_preserves_filing_provenance() -> None:
    frame, _ = extract_current_ttm_duration_candidates(
        _payload(),
        ticker="AAA",
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 8, 1),
        accepted_at="2026-08-01T12:00:00-04:00",
    )

    assert set(frame["adsh"]) == {"0001"}
    assert set(frame["filed_date"]) == {"2026-08-01"}
    assert set(frame["accepted_at"]) == {"2026-08-01T12:00:00-04:00"}
    assert set(frame["period_date"]) == {"2026-06-30"}
