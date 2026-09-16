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


def _common_fake(mapping, merge_base_result=True):
    def fake_git(repo_root, *args):
        return mapping[args]

    def fake_success(repo_root, *args):
        assert args == ("merge-base", "--is-ancestor", "upstream/master", "HEAD")
        return merge_base_result

    return fake_git, fake_success


def test_clean_upstream_state_is_ready(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    mapping = {
        ("remote",): "origin\nupstream",
        ("fetch", "--quiet", "upstream"): "",
        ("rev-parse", "HEAD"): "same123",
        ("rev-parse", "upstream/master"): "same123",
        (
            "diff",
            "--name-only",
            "HEAD...upstream/master",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
        (
            "status",
            "--porcelain",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
    }
    fake_git, fake_success = _common_fake(mapping)
    monkeypatch.setattr(freshness, "_run_git", fake_git)
    monkeypatch.setattr(freshness, "_git_success", fake_success)

    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is True
    assert result.reason == "pitindex_universe_current"
    assert result.local_commit == "same123"
    assert result.upstream_commit == "same123"
    assert result.changed_relevant_paths == ()
    assert result.dirty_relevant_paths == ()
    assert result.upstream_in_local_history is True


def test_relevant_upstream_drift_blocks(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    mapping = {
        ("remote",): "origin\nupstream",
        ("fetch", "--quiet", "upstream"): "",
        ("rev-parse", "HEAD"): "local123",
        ("rev-parse", "upstream/master"): "upstream456",
        (
            "diff",
            "--name-only",
            "HEAD...upstream/master",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "pitindex/data/sp500_current.csv",
        (
            "status",
            "--porcelain",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
    }
    fake_git, fake_success = _common_fake(mapping, merge_base_result=False)
    monkeypatch.setattr(freshness, "_run_git", fake_git)
    monkeypatch.setattr(freshness, "_git_success", fake_success)

    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is False
    assert result.reason == "upstream_universe_drift_requires_review"
    assert result.changed_relevant_paths == ("pitindex/data/sp500_current.csv",)


def test_reviewed_local_correction_does_not_block(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    mapping = {
        ("remote",): "origin\nupstream",
        ("fetch", "--quiet", "upstream"): "",
        ("rev-parse", "HEAD"): "local-correction",
        ("rev-parse", "upstream/master"): "reviewed-upstream",
        (
            "diff",
            "--name-only",
            "HEAD...upstream/master",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
        (
            "status",
            "--porcelain",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
    }
    fake_git, fake_success = _common_fake(mapping, merge_base_result=True)
    monkeypatch.setattr(freshness, "_run_git", fake_git)
    monkeypatch.setattr(freshness, "_git_success", fake_success)

    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is True
    assert result.reason == "pitindex_upstream_reviewed_with_local_corrections"
    assert result.upstream_in_local_history is True
    assert result.local_commit != result.upstream_commit


def test_irrelevant_upstream_drift_does_not_block(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    mapping = {
        ("remote",): "origin\nupstream",
        ("fetch", "--quiet", "upstream"): "",
        ("rev-parse", "HEAD"): "local123",
        ("rev-parse", "upstream/master"): "upstream789",
        (
            "diff",
            "--name-only",
            "HEAD...upstream/master",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
        (
            "status",
            "--porcelain",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
    }
    fake_git, fake_success = _common_fake(mapping, merge_base_result=False)
    monkeypatch.setattr(freshness, "_run_git", fake_git)
    monkeypatch.setattr(freshness, "_git_success", fake_success)

    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is True
    assert result.changed_relevant_paths == ()


def test_dirty_relevant_file_blocks(monkeypatch, tmp_path):
    _, data = _make_repo(tmp_path)
    mapping = {
        ("remote",): "origin\nupstream",
        ("fetch", "--quiet", "upstream"): "",
        ("rev-parse", "HEAD"): "local123",
        ("rev-parse", "upstream/master"): "upstream123",
        (
            "diff",
            "--name-only",
            "HEAD...upstream/master",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): "",
        (
            "status",
            "--porcelain",
            "--",
            "pitindex/data/sp500_current.csv",
            "pitindex/data/sp500_changes.csv",
        ): " M pitindex/data/sp500_changes.csv",
    }
    fake_git, fake_success = _common_fake(mapping, merge_base_result=True)
    monkeypatch.setattr(freshness, "_run_git", fake_git)
    monkeypatch.setattr(freshness, "_git_success", fake_success)

    result = freshness.evaluate_pitindex_freshness(data)

    assert result.ready is False
    assert result.reason == "pitindex_relevant_files_have_uncommitted_changes"
    assert result.dirty_relevant_paths == ("pitindex/data/sp500_changes.csv",)


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
