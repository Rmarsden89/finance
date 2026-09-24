import json
from pathlib import Path

from finance.research.fingerprints import (
    companyfacts_paths,
    fingerprint_differences,
    fingerprint_files,
)


def test_file_set_fingerprint_is_order_independent_and_detects_change(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    first.write_text("a\n1\n", encoding="utf-8")
    second.write_text("b\n2\n", encoding="utf-8")

    left = fingerprint_files(root=tmp_path, paths=[first, second])
    right = fingerprint_files(root=tmp_path, paths=[second, first])

    assert left["sha256"] == right["sha256"]
    assert left["file_count"] == 2

    second.write_text("b\n3\n", encoding="utf-8")
    changed = fingerprint_files(root=tmp_path, paths=[first, second])
    assert changed["sha256"] != left["sha256"]


def test_companyfacts_fingerprint_scope_uses_only_cached_discovery_rows(
    tmp_path: Path,
) -> None:
    discovery = tmp_path / "discovery.csv"
    discovery.write_text(
        "ticker,cik,status\nAAA,1,new_filing_cached\nBBB,2,no_new_filing\n"
        "AAA,1,new_filing_cached\n",
        encoding="utf-8",
    )

    paths = companyfacts_paths(discovery, tmp_path / "cache")

    assert paths == [tmp_path / "cache" / "companyfacts" / "CIK0000000001.json"]


def test_fingerprint_difference_names_changed_group_and_code() -> None:
    expected = {
        "schema_version": 1,
        "groups": {"discovery": {"sha256": "a"}},
        "code": {"commit": "one", "tracked_worktree_clean": True},
    }
    actual = {
        "schema_version": 1,
        "groups": {"discovery": {"sha256": "b"}},
        "code": {"commit": "two", "tracked_worktree_clean": False},
    }

    assert fingerprint_differences(expected, actual) == [
        "discovery",
        "code.commit",
        "code.tracked_worktree_clean",
    ]
