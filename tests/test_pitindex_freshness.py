from pathlib import Path

import pytest

from finance.data import pitindex_freshness as freshness


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "pitindex"
    data = repo / "pitindex" / "data"
    data.mkdir(parents=True)
    (repo / ".git").mkdir()
    (data / "sp500_current.csv").write_text("ticker\nAAA\n", encoding="utf-8")
    (data / "sp500_changes.csv").write_text("date,ticker\n", encoding="utf-8")
    return repo, data


def test_clean_upstream_state_is_ready(monkeypatch, tmp_path):
    repo, data = _make_repo(tmp_path)
    calls = []

    def fake_git(repo_root, *args):
        calls.append(args)
        mapping = {
            ("remote",): "origin\nupstream",
            ("fetch", "--quiet", "upstream"): "",
            ("rev-parse", "HEAD"): "local123",
            ("rev-parse", "upstream/master"): "upstream456",
            (
                "diff",
                "--name-only",
                "HEAD..upstream/master",
                "--",
                "pitindex/data/sp500_current.csv",
                "pitindex/data/sp500_changes.csv",
            ): "",
        }
        return mapping[args]

    monkeypatch.setattr(freshness, "_run_git", fake_git)
    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is True
    assert result.reason == "pitindex_universe_current"
    assert result.local_commit == "local123"
    assert result.upstream_commit == "upstream456"
    assert result.changed_relevant_paths == ()
    assert set(result.file_sha256) == {
        "pitindex/data/sp500_current.csv",
        "pitindex/data/sp500_changes.csv",
    }
    assert ("fetch", "--quiet", "upstream") in calls


def test_relevant_upstream_drift_blocks(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)

    def fake_git(repo_root, *args):
        if args == ("remote",):
            return "origin\nupstream"
        if args == ("fetch", "--quiet", "upstream"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "local123"
        if args == ("rev-parse", "upstream/master"):
            return "upstream456"
        if args[0] == "diff":
            return "pitindex/data/sp500_current.csv"
        raise AssertionError(args)

    monkeypatch.setattr(freshness, "_run_git", fake_git)
    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is False
    assert result.reason == "upstream_universe_drift_requires_review"
    assert result.changed_relevant_paths == ("pitindex/data/sp500_current.csv",)


def test_irrelevant_upstream_drift_does_not_block(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)

    def fake_git(repo_root, *args):
        mapping = {
            ("remote",): "origin\nupstream",
            ("fetch", "--quiet", "upstream"): "",
            ("rev-parse", "HEAD"): "local123",
            ("rev-parse", "upstream/master"): "upstream789",
        }
        if args[0] == "diff":
            return ""
        return mapping[args]

    monkeypatch.setattr(freshness, "_run_git", fake_git)
    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is True
    assert result.local_commit != result.upstream_commit
    assert result.changed_relevant_paths == ()


def test_missing_upstream_remote_fails_closed(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    monkeypatch.setattr(freshness, "_run_git", lambda repo_root, *args: "origin")

    with pytest.raises(freshness.PitindexFreshnessError, match="upstream"):
        freshness.evaluate_pitindex_freshness(data)


def test_fetch_failure_surfaces_as_fail_closed(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)

    def fake_git(repo_root, *args):
        if args == ("remote",):
            return "origin\nupstream"
        if args == ("fetch", "--quiet", "upstream"):
            raise freshness.PitindexFreshnessError("network unavailable")
        raise AssertionError(args)

    monkeypatch.setattr(freshness, "_run_git", fake_git)
    with pytest.raises(freshness.PitindexFreshnessError, match="network unavailable"):
        freshness.evaluate_pitindex_freshness(data)
