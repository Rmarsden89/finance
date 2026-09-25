from __future__ import annotations

import pandas as pd

from finance.research.share_historical_validation import (
    _segment_members,
    select_pit_share_candidate,
)


def test_segment_members_fail_closed_and_extracts_share_class_member() -> None:
    assert _segment_members("") == []

    parsed = _segment_members(
        '[{"dimension":"dei:LegalEntityAxis",'
        '"member":"us-gaap:ClassACommonStockMember"}]'
    )
    assert parsed == ["us-gaap:ClassACommonStockMember"]

    assert _segment_members("opaque-segment-without-member") is None


def test_select_pit_share_candidate_uses_latest_available_candidate() -> None:
    filings = pd.DataFrame(
        [
            {
                "status": "candidate",
                "candidate_shares": 100.0,
                "available_at": "2026-08-01T06:00:00-04:00",
                "context_instant": "2026-07-31",
                "accepted_at": "2026-07-31T17:00:00",
                "adsh": "old",
            },
            {
                "status": "candidate",
                "candidate_shares": 120.0,
                "available_at": "2026-09-10T06:00:00-04:00",
                "context_instant": "2026-09-09",
                "accepted_at": "2026-09-09T17:00:00",
                "adsh": "new",
            },
            {
                "status": "candidate",
                "candidate_shares": 130.0,
                "available_at": "2026-09-20T06:00:00-04:00",
                "context_instant": "2026-09-19",
                "accepted_at": "2026-09-19T17:00:00",
                "adsh": "future",
            },
        ]
    )

    selected = select_pit_share_candidate(
        filings,
        decision_cutoff=pd.Timestamp("2026-09-15T16:00:00-04:00"),
        decision_date=pd.Timestamp("2026-09-15"),
    )

    assert selected is not None
    assert selected["adsh"] == "new"
    assert selected["candidate_shares"] == 120.0
