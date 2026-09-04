from pathlib import Path

from finance.data.sources.historical_identity import (
    classify_identity_case,
    load_identity_context,
)


def test_classifies_merger_and_bankruptcy() -> None:
    assert classify_identity_case(
        reason="Removed after merger with another company",
        has_rename_successor=False,
    ) == "merger"
    assert classify_identity_case(
        reason="Company entered bankruptcy proceedings",
        has_rename_successor=False,
    ) == "bankruptcy_or_failure"


def test_rename_successor_is_flagged_for_review(tmp_path: Path) -> None:
    changes = tmp_path / "sp500_changes.csv"
    changes.write_text(
        "date,action,ticker,name,reason\n"
        "2020-01-01,removed,OLD,Old Co,rename\n",
        encoding="utf-8",
    )
    renames = tmp_path / "ticker_renames.csv"
    renames.write_text(
        "date,old_ticker,new_ticker,reason\n"
        "2020-01-01,OLD,NEW,Old Co renamed New Co\n",
        encoding="utf-8",
    )

    context = load_identity_context(
        changes_path=changes,
        rename_path=renames,
    )["OLD"]

    assert context.rename_successor == "NEW"
    assert context.category == "rename_or_successor_review"
