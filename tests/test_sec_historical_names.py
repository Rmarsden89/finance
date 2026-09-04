from datetime import date
from pathlib import Path

from finance.data import MembershipInterval
from finance.data.sources.sec_historical_names import load_sec_historical_name_map


def test_unique_historical_name_resolves(tmp_path: Path) -> None:
    path = tmp_path / "cik-lookup-data.txt"
    path.write_text(
        "ABIOMED INC:0000815094:\n"
        "OTHER CO:0000123456:\n",
        encoding="latin-1",
    )

    result = load_sec_historical_name_map(
        path,
        unresolved=[
            MembershipInterval(
                "sp500",
                "ABMD",
                date(2018, 1, 1),
                None,
                company_name="Abiomed",
            )
        ],
    )

    assert result == {"ABMD": 815094}


def test_ambiguous_name_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cik-lookup-data.txt"
    path.write_text(
        "SHARED INC:0000000001:\n"
        "SHARED CORP:0000000002:\n",
        encoding="latin-1",
    )

    result = load_sec_historical_name_map(
        path,
        unresolved=[
            MembershipInterval(
                "sp500",
                "OLD",
                date(2018, 1, 1),
                None,
                company_name="Shared",
            )
        ],
    )

    assert result == {}
