from datetime import date

from finance.data.sec_current_facts import extract_companyfacts_candidates


def test_extract_companyfacts_candidates_filters_accession_and_period() -> None:
    payload = {
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2026-04-27",
                                "end": "2026-07-26",
                                "val": 100,
                            },
                            {
                                "accn": "OLD",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q3",
                                "start": "2025-04-28",
                                "end": "2025-07-27",
                                "val": 90,
                            },
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "end": "2026-07-26",
                                "val": 500,
                            }
                        ]
                    }
                },
            }
        },
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 7, 26),
        filed_date=date(2026, 8, 20),
        accepted_at="2026-08-20T16:01:46",
    )

    assert set(frame["concept"]) == {"revenue", "total_assets"}
    assert dict(zip(frame["concept"], frame["qtrs"])) == {
        "revenue": 1,
        "total_assets": 0,
    }
    assert audit.rows_matching_accession == 2
    assert audit.rows_output == 2


def test_ytd_cash_flow_uses_fp_qtrs() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 123,
                            }
                        ]
                    }
                }
            }
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    assert frame.iloc[0]["concept"] == "operating_cash_flow"
    assert frame.iloc[0]["qtrs"] == 2



def test_quarterly_income_rejects_ytd_duration() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2026-04-27",
                                "end": "2026-07-26",
                                "val": 100,
                            },
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2025-10-27",
                                "end": "2026-07-26",
                                "val": 300,
                            },
                        ]
                    }
                }
            }
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 7, 26),
        filed_date=date(2026, 8, 20),
        accepted_at="2026-08-20T10:00:00",
    )

    assert len(frame) == 1
    assert frame.iloc[0]["value"] == 100
    assert frame.iloc[0]["qtrs"] == 1
    assert frame.iloc[0]["duration_days"] == 91
