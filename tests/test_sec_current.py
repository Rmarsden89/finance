from finance.data.sources.sec_current import (
    parse_acceptance_datetime,
    recent_filings_from_submissions,
)


def test_parse_acceptance_datetime() -> None:
    header = "<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20260820160237\n"
    assert parse_acceptance_datetime(header).isoformat() == "2026-08-20T16:02:37"


def test_recent_filings_filters_supported_forms() -> None:
    payload = {
        "cik": "6951",
        "filings": {
            "recent": {
                "accessionNumber": ["0001", "0002", "0003"],
                "form": ["10-Q", "8-K", "10-K/A"],
                "filingDate": ["2026-08-20", "2026-08-20", "2026-01-01"],
                "reportDate": ["2026-07-26", "2026-07-26", "2025-10-26"],
                "primaryDocument": ["q.htm", "8k.htm", "k.htm"],
            }
        },
    }

    rows = recent_filings_from_submissions(payload)
    assert [row.accession for row in rows] == ["0001", "0003"]
    assert rows[0].cik == 6951
    assert rows[0].report_date.isoformat() == "2026-07-26"
