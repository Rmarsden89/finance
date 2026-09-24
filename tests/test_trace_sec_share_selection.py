from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from scripts.trace_sec_share_selection import trace_share_selection


def _submissions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "adsh": "spgi-filing",
                "cik": 64040,
                "name": "S&P Global Inc.",
                "form": "10-Q",
                "period_date": date(2026, 3, 31),
                "filed_date": date(2026, 4, 28),
                "accepted_at": datetime(2026, 4, 28, 17, 18),
            }
        ]
    )


def _numeric() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "adsh": "spgi-filing",
                "tag": "CommonStockSharesOutstanding",
                "version": "us-gaap/2025",
                "ddate_date": date(2026, 3, 31),
                "qtrs": 0,
                "uom": "shares",
                "segments": "",
                "coreg": "",
                "value": 296_000_000,
            }
        ]
    )


def test_trace_identifies_statement_placement_as_first_rejection() -> None:
    presentation = pd.DataFrame(
        [
            {
                "adsh": "spgi-filing",
                "tag": "CommonStockSharesOutstanding",
                "version": "us-gaap/2025",
                "stmt": "EQ",
            }
        ]
    )

    trace = trace_share_selection(
        _submissions(),
        _numeric(),
        presentation,
        cik=64040,
    )

    assert len(trace) == 1
    assert trace.iloc[0]["presentation_statements"] == "EQ"
    assert trace.iloc[0]["first_rejection_stage"] == "statement_placement"
    assert bool(trace.iloc[0]["canonical_eligible"]) is False


def test_trace_accepts_cover_page_statement() -> None:
    presentation = pd.DataFrame(
        [
            {
                "adsh": "spgi-filing",
                "tag": "CommonStockSharesOutstanding",
                "version": "us-gaap/2025",
                "stmt": "CP",
            }
        ]
    )

    trace = trace_share_selection(
        _submissions(),
        _numeric(),
        presentation,
        cik=64040,
    )

    assert trace.iloc[0]["first_rejection_stage"] == "canonical_eligible"
    assert bool(trace.iloc[0]["canonical_eligible"]) is True
