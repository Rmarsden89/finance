import pandas as pd

from scripts.compare_sec_current_zip_parity import compare_frames


def _frame(value: int, tag: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "cik": 1,
                "adsh": "A",
                "concept": "revenue",
                "ddate_date": "2026-06-30",
                "qtrs": 1,
                "uom": "USD",
                "value": value,
                "source_tag": tag,
            }
        ]
    )


def test_exact_match() -> None:
    rows = compare_frames(
        ticker="AAA",
        archived=_frame(100, "Revenues"),
        current=_frame(100, "Revenues"),
    )
    assert rows[0]["status"] == "exact_match"


def test_value_match_tag_difference() -> None:
    rows = compare_frames(
        ticker="AAA",
        archived=_frame(100, "Revenues"),
        current=_frame(
            100,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ),
    )
    assert rows[0]["status"] == "value_match_tag_difference"


def test_value_mismatch() -> None:
    rows = compare_frames(
        ticker="AAA",
        archived=_frame(100, "Revenues"),
        current=_frame(101, "Revenues"),
    )
    assert rows[0]["status"] == "value_mismatch"
