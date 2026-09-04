from datetime import date
import csv
import gzip
from pathlib import Path

from finance.data import MembershipInterval
from finance.data.sources.datamule import load_datamule_identity_map


def _write_gzip_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_resolves_unique_ticker_match(tmp_path: Path) -> None:
    metadata = tmp_path / "listed_filer_metadata.csv.gz"
    names = tmp_path / "listed_filer_names.csv.gz"

    _write_gzip_csv(
        metadata,
        ["name", "cik", "tickers"],
        [{"name": "Old Company Inc.", "cik": "123", "tickers": "['OLD']"}],
    )
    _write_gzip_csv(
        names,
        ["cik", "name", "start_date", "end_date"],
        [],
    )

    result = load_datamule_identity_map(
        metadata_path=metadata,
        names_path=names,
        unresolved=[
            MembershipInterval("sp500", "OLD", date(2015, 1, 1), None)
        ],
    )

    assert result == {"OLD": 123}


def test_resolves_unique_former_name_match(tmp_path: Path) -> None:
    metadata = tmp_path / "listed_filer_metadata.csv.gz"
    names = tmp_path / "listed_filer_names.csv.gz"

    _write_gzip_csv(
        metadata,
        ["name", "cik", "tickers"],
        [{"name": "New Name Corp.", "cik": "456", "tickers": "['NEW']"}],
    )
    _write_gzip_csv(
        names,
        ["cik", "name", "start_date", "end_date"],
        [
            {
                "cik": "456",
                "name": "Old Name Corporation",
                "start_date": "2010-01-01",
                "end_date": "2019-01-01",
            }
        ],
    )

    result = load_datamule_identity_map(
        metadata_path=metadata,
        names_path=names,
        unresolved=[
            MembershipInterval(
                "sp500",
                "OLD",
                date(2015, 1, 1),
                None,
                company_name="Old Name Corp.",
            )
        ],
    )

    assert result == {"OLD": 456}


def test_ambiguous_name_is_not_resolved(tmp_path: Path) -> None:
    metadata = tmp_path / "listed_filer_metadata.csv.gz"
    names = tmp_path / "listed_filer_names.csv.gz"

    _write_gzip_csv(
        metadata,
        ["name", "cik", "tickers"],
        [],
    )
    _write_gzip_csv(
        names,
        ["cik", "name", "start_date", "end_date"],
        [
            {"cik": "1", "name": "Shared Co", "start_date": "", "end_date": ""},
            {"cik": "2", "name": "Shared Company", "start_date": "", "end_date": ""},
        ],
    )

    result = load_datamule_identity_map(
        metadata_path=metadata,
        names_path=names,
        unresolved=[
            MembershipInterval(
                "sp500",
                "OLD",
                date(2015, 1, 1),
                None,
                company_name="Shared Corp.",
            )
        ],
    )

    assert result == {}
