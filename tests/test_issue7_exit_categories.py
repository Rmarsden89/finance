from __future__ import annotations

from finance.data.sources.historical_identity import classify_identity_case


def test_issue7_exit_categories_use_conservative_pit_reason_classes() -> None:
    assert classify_identity_case(
        reason="Company acquired by buyer",
        has_rename_successor=False,
    ) == "acquisition"
    assert classify_identity_case(
        reason="Removed after merger",
        has_rename_successor=False,
    ) == "merger"
    assert classify_identity_case(
        reason="Ticker change",
        has_rename_successor=True,
    ) == "rename_or_successor_review"
    assert classify_identity_case(
        reason="Committee replacement",
        has_rename_successor=False,
    ) == "historical_identity_research"
