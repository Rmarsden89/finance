from __future__ import annotations

import pandas as pd

from scripts.trace_sec_current_shares import build_current_share_trace


def test_trace_identifies_companyfacts_candidate_merge_and_snapshot_gap() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "0000064040-26-000099",
                                "end": "2026-06-30",
                                "filed": "2026-07-29",
                                "form": "10-Q",
                                "val": 294_000_000,
                            }
                        ]
                    }
                }
            }
        }
    }
    discovery = pd.DataFrame([
        {
            "ticker": "SPGI",
            "cik": 64040,
            "status": "new_filing_cached",
            "accession": "0000064040-26-000099",
            "form": "10-Q",
            "report_date": "2026-06-30",
            "filing_date": "2026-07-29",
            "accepted_at": "2026-07-29T17:00:00",
            "error": "",
        }
    ])
    candidates = pd.DataFrame([
        {
            "ticker": "SPGI",
            "cik": 64040,
            "adsh": "0000064040-26-000099",
            "concept": "shares_outstanding",
            "source_tag": "EntityCommonStockSharesOutstanding",
            "value": 294_000_000,
            "uom": "shares",
            "form": "10-Q",
            "ddate_date": "2026-06-30",
            "accepted_at": "2026-07-29T17:00:00",
        }
    ])
    merge_audit = pd.DataFrame([
        {
            "ticker": "SPGI",
            "cik": 64040,
            "adsh": "0000064040-26-000099",
            "concept": "shares_outstanding",
            "source_tag": "EntityCommonStockSharesOutstanding",
            "value": 294_000_000,
            "uom": "shares",
            "ddate_date": "2026-06-30",
            "accepted_at": "2026-07-29T17:00:00",
            "status": "added_current_winner",
            "reason": "",
        }
    ])
    shadow_winners = candidates.drop(columns=["ticker"]).copy()
    current_snapshot = pd.DataFrame([
        {"ticker": "SPGI", "cik": 64040, "shares_outstanding": ""}
    ])

    trace = build_current_share_trace(
        payload=payload,
        discovery=discovery,
        candidates=candidates,
        merge_audit=merge_audit,
        shadow_winners=shadow_winners,
        current_snapshot=current_snapshot,
        ticker="SPGI",
        cik=64040,
        as_of=pd.Timestamp("2026-09-15"),
    )

    statuses = set(zip(trace["stage"], trace["status"]))
    assert ("companyfacts", "observation") in statuses
    assert ("candidate_extraction", "candidate") in statuses
    assert ("shadow_merge", "added_current_winner") in statuses
    assert ("shadow_winners", "winner") in statuses
    assert ("current_snapshot", "share_value_missing") in statuses
    companyfacts = trace.loc[trace["stage"].eq("companyfacts")].iloc[0]
    assert companyfacts["taxonomy"] == "dei"
    assert companyfacts["source_tag"] == "EntityCommonStockSharesOutstanding"


def test_trace_reports_missing_pipeline_stages_without_mutating_inputs() -> None:
    empty = pd.DataFrame()
    trace = build_current_share_trace(
        payload={"facts": {"us-gaap": {}}},
        discovery=empty,
        candidates=empty,
        merge_audit=empty,
        shadow_winners=empty,
        current_snapshot=empty,
        ticker="SPGI",
        cik=64040,
        as_of=pd.Timestamp("2026-09-15"),
    )

    assert list(trace["status"]) == [
        "no_share_observations",
        "no_matching_row",
        "no_share_candidate",
        "no_share_audit_row",
        "no_share_winner",
        "company_missing",
    ]
