from pathlib import Path

import pandas as pd

from finance.research.share_cleanup import (
    classify_residual_shares,
    normalize_v2_nonpositive_share_winners,
)


def test_v2_nonpositive_share_winners_become_missing_with_audit() -> None:
    winners = pd.DataFrame(
        [
            {"ticker": "ZERO", "concept": "shares_outstanding", "value": 0},
            {"ticker": "NEG", "concept": "shares_outstanding", "value": -1},
            {"ticker": "GOOD", "concept": "shares_outstanding", "value": 10},
            {"ticker": "LOSS", "concept": "net_income", "value": -5},
        ]
    )

    normalized, audit = normalize_v2_nonpositive_share_winners(winners)

    lookup = normalized.set_index("ticker")
    assert pd.isna(lookup.loc["ZERO", "value"])
    assert pd.isna(lookup.loc["NEG", "value"])
    assert lookup.loc["GOOD", "value"] == 10
    assert lookup.loc["LOSS", "value"] == -5
    assert audit["ticker"].tolist() == ["ZERO", "NEG"]
    assert audit["original_value"].tolist() == [0, -1]
    assert audit["quality_adjustment"].nunique() == 1


def test_residual_cleanup_separates_recoverable_and_defensible_gaps(
    tmp_path: Path,
) -> None:
    snapshot = pd.DataFrame(
        [
            {"ticker": "GOOD", "cik": 1, "company_name": "Good", "shares_outstanding": 10},
            {"ticker": "NODISC", "cik": 2, "company_name": "No discovery", "shares_outstanding": None},
            {"ticker": "NOCACHE", "cik": 3, "company_name": "No cache", "shares_outstanding": None},
            {"ticker": "BUG", "cik": 4, "company_name": "Bug", "shares_outstanding": None},
            {"ticker": "NOFACT", "cik": 5, "company_name": "No fact", "shares_outstanding": None},
            {"ticker": "ZERO", "cik": 6, "company_name": "Zero", "shares_outstanding": 0},
            {"ticker": "NONEW", "cik": 7, "company_name": "No new filing", "shares_outstanding": None},
        ]
    )
    discovery = pd.DataFrame(
        [
            {"ticker": ticker, "status": "new_filing_cached", "accession": ticker}
            for ticker in ("NOCACHE", "BUG", "NOFACT", "ZERO")
        ]
        + [{"ticker": "NONEW", "status": "no_new_filing", "accession": ""}]
    )
    audit = pd.DataFrame(
        [
            {"ticker": ticker, "status": "ok"}
            for ticker in ("BUG", "NOFACT", "ZERO")
        ]
    )
    candidates = pd.DataFrame(
        [
            {"ticker": "BUG", "concept": "shares_outstanding"},
            {"ticker": "NOFACT", "concept": "revenue"},
        ]
    )
    companyfacts = tmp_path / "companyfacts"
    companyfacts.mkdir()
    for cik in (4, 5, 6):
        (companyfacts / f"CIK{cik:010d}.json").write_text(
            "{}", encoding="utf-8"
        )

    detail, summary = classify_residual_shares(
        snapshot=snapshot,
        discovery=discovery,
        candidate_audit=audit,
        candidates=candidates,
        cache_dir=tmp_path,
    )
    lookup = detail.set_index("ticker")

    assert lookup.loc["NODISC", "recommended_action"] == "targeted_sec_refresh"
    assert lookup.loc["NOCACHE", "classification"] == "missing_companyfacts_cache"
    assert lookup.loc["BUG", "recommended_action"] == "candidate_not_selected"
    assert lookup.loc["NOFACT", "recommended_action"] == "documented_no_supported_fact"
    assert lookup.loc["ZERO", "recommended_action"] == "investigate_invalid_value"
    assert lookup.loc["NONEW", "classification"] == "no_new_supported_filing"
    assert lookup.loc["NONEW", "recommended_action"] == "documented_no_supported_fact"
    assert summary.positive_shares == 1
    assert summary.residual_rows == 6
    assert summary.targeted_sec_refresh == 2
    assert summary.investigate_invalid_value == 1
    assert summary.candidate_not_selected == 1
    assert summary.documented_no_supported_fact == 2


def test_cleanup_scripts_have_no_broker_or_order_imports() -> None:
    root = Path(__file__).parents[1]
    sources = "\n".join(
        (root / "scripts" / name).read_text(encoding="utf-8")
        for name in (
            "audit_v2_residual_shares.py",
            "refresh_v2_share_gaps.py",
        )
    )
    prohibited = ("finance.broker", "finance.shadow", "place_equity_order")
    assert not any(value in sources for value in prohibited)
