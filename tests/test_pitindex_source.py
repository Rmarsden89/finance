from datetime import date
from pathlib import Path

from finance.data.membership import MembershipStore
from finance.data.sources.pitindex import load_pitindex_sp500


def test_pitindex_events_become_membership_intervals(tmp_path: Path) -> None:
    (tmp_path / "sp500_seed.csv").write_text(
        "effective_date,ticker\n"
        "2014-12-30,AAA\n"
        "2014-12-30,BBB\n",
        encoding="utf-8",
    )
    (tmp_path / "sp500_changes.csv").write_text(
        "date,action,ticker,name,reason\n"
        "2015-06-01,removed,BBB,Beta,change\n"
        "2015-06-01,added,CCC,Gamma,change\n",
        encoding="utf-8",
    )
    (tmp_path / "sp500_current.csv").write_text(
        "ticker,name,cik,gics_sector,gics_sub_industry,date_added\n"
        "AAA,Alpha,0000000001,,,\n"
        "CCC,Gamma,0000000003,,,\n",
        encoding="utf-8",
    )

    intervals = load_pitindex_sp500(tmp_path)
    store = MembershipStore(intervals)

    assert [r.ticker for r in store.members_as_of(date(2015, 5, 31))] == [
        "AAA",
        "BBB",
    ]
    assert [r.ticker for r in store.members_as_of(date(2015, 6, 1))] == [
        "AAA",
        "CCC",
    ]

    assert store.coverage(date(2015, 5, 31))["cik_coverage"] == 0.5
    assert store.coverage(date(2015, 6, 1))["cik_coverage"] == 1.0


def test_sec_map_can_enrich_historical_ticker(tmp_path: Path) -> None:
    (tmp_path / "sp500_seed.csv").write_text(
        "effective_date,ticker\n2014-12-30,OLD\n",
        encoding="utf-8",
    )
    (tmp_path / "sp500_changes.csv").write_text(
        "date,action,ticker,name,reason\n",
        encoding="utf-8",
    )
    (tmp_path / "sp500_current.csv").write_text(
        "ticker,name,cik,gics_sector,gics_sub_industry,date_added\n",
        encoding="utf-8",
    )

    intervals = load_pitindex_sp500(
        tmp_path,
        sec_cik_by_ticker={"OLD": 123},
    )

    assert intervals[0].cik == 123
