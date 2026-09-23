from __future__ import annotations

import csv
from pathlib import Path

from scripts.promote_issue19_market_candidate import (
    _atomic_replace_from,
    _coverage_counts,
    _copy_verified,
)


def test_issue19_coverage_counts(tmp_path: Path) -> None:
    path = tmp_path / "coverage.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pit_ticker", "selected_source"],
        )
        writer.writeheader()
        writer.writerows([
            {"pit_ticker": "AAA", "selected_source": "tiingo"},
            {"pit_ticker": "BBB", "selected_source": "tiingo"},
            {"pit_ticker": "CCC", "selected_source": "unresolved"},
        ])

    counts = _coverage_counts(path)

    assert counts == {
        "coverage_rows": 3,
        "tiingo_selected": 2,
        "stooq_selected": 0,
        "unresolved_tickers": 1,
    }


def test_issue19_verified_copy_and_atomic_replace(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    backup = tmp_path / "backup.bin"

    source.write_bytes(b"candidate")
    destination.write_bytes(b"baseline")

    _copy_verified(destination, backup)
    _atomic_replace_from(source, destination)

    assert backup.read_bytes() == b"baseline"
    assert destination.read_bytes() == b"candidate"
    assert not (tmp_path / "destination.bin.issue19.tmp").exists()
