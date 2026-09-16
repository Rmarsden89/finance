from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Any


class PitindexFreshnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class PitindexProvenance:
    repo_root: str
    data_dir: str
    upstream_remote: str
    upstream_ref: str
    local_commit: str
    upstream_commit: str
    relevant_paths: tuple[str, ...]
    changed_relevant_paths: tuple[str, ...]
    file_sha256: dict[str, str]
    ready: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["relevant_paths"] = list(self.relevant_paths)
        payload["changed_relevant_paths"] = list(self.changed_relevant_paths)
        return payload


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PitindexFreshnessError("git executable is not available") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise PitindexFreshnessError(
            f"PITIndex git command failed: git {' '.join(args)}"
            + (f"; {detail}" if detail else "")
        ) from exc
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_root(data_dir: Path) -> Path:
    resolved = data_dir.resolve()
    current = resolved
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise PitindexFreshnessError(
        f"Unable to locate PITIndex git repository above {resolved}"
    )


def evaluate_pitindex_freshness(
    data_dir: Path,
    *,
    upstream_remote: str = "upstream",
    upstream_branch: str = "master",
    fetch: bool = True,
) -> PitindexProvenance:
    """Verify the local PITIndex universe files have no unreviewed upstream drift.

    The gate intentionally fetches and compares only. It never merges, pulls, resets,
    or modifies the PITIndex working tree.
    """
    data_dir = data_dir.resolve()
    repo_root = resolve_repo_root(data_dir)
    try:
        data_relative = data_dir.relative_to(repo_root)
    except ValueError as exc:  # pragma: no cover - guarded by resolve_repo_root
        raise PitindexFreshnessError("PITIndex data directory is outside its repository") from exc

    relevant_paths = tuple(
        (data_relative / filename).as_posix()
        for filename in ("sp500_current.csv", "sp500_changes.csv")
    )
    for relative in relevant_paths:
        path = repo_root / Path(relative)
        if not path.exists():
            raise PitindexFreshnessError(f"Required PITIndex source file is missing: {path}")

    remotes = set(_run_git(repo_root, "remote").splitlines())
    if upstream_remote not in remotes:
        raise PitindexFreshnessError(
            f"PITIndex remote {upstream_remote!r} is not configured. "
            "Add the authoritative upstream remote before running V1."
        )

    if fetch:
        _run_git(repo_root, "fetch", "--quiet", upstream_remote)

    upstream_ref = f"{upstream_remote}/{upstream_branch}"
    local_commit = _run_git(repo_root, "rev-parse", "HEAD")
    upstream_commit = _run_git(repo_root, "rev-parse", upstream_ref)

    changed_output = _run_git(
        repo_root,
        "diff",
        "--name-only",
        f"HEAD..{upstream_ref}",
        "--",
        *relevant_paths,
    )
    changed_relevant = tuple(
        line.strip().replace("\\", "/")
        for line in changed_output.splitlines()
        if line.strip()
    )

    file_hashes = {
        relative: _sha256(repo_root / Path(relative))
        for relative in relevant_paths
    }
    ready = not changed_relevant
    reason = "pitindex_universe_current" if ready else "upstream_universe_drift_requires_review"

    return PitindexProvenance(
        repo_root=str(repo_root),
        data_dir=str(data_dir),
        upstream_remote=upstream_remote,
        upstream_ref=upstream_ref,
        local_commit=local_commit,
        upstream_commit=upstream_commit,
        relevant_paths=relevant_paths,
        changed_relevant_paths=changed_relevant,
        file_sha256=file_hashes,
        ready=ready,
        reason=reason,
    )
