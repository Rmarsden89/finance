from pathlib import Path

from finance.data.sources.ticker_aliases import load_safe_ticker_aliases


def test_safe_plain_rename_resolves_to_successor_cik(tmp_path: Path) -> None:
    path = tmp_path / "ticker_renames.csv"
    path.write_text(
        "date,old_ticker,new_ticker,reason\n"
        "2022-06-09,FB,META,Facebook renamed Meta Platforms.\n",
        encoding="utf-8",
    )

    aliases = load_safe_ticker_aliases(
        path,
        cik_by_ticker={"META": 1326801},
    )

    assert aliases == {"FB": 1326801}


def test_merger_is_not_auto_resolved(tmp_path: Path) -> None:
    path = tmp_path / "ticker_renames.csv"
    path.write_text(
        "date,old_ticker,new_ticker,reason\n"
        "2020-04-03,UTX,RTX,United Technologies + Raytheon merger forming Raytheon Technologies.\n",
        encoding="utf-8",
    )

    aliases = load_safe_ticker_aliases(
        path,
        cik_by_ticker={"RTX": 101829},
    )

    assert aliases == {}
