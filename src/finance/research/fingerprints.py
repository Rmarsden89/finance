from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable


FINGERPRINT_SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_files(
    *,
    root: Path,
    paths: Iterable[Path],
) -> dict[str, object]:
    """Fingerprint an explicit deterministic file set."""

    root = root.resolve()
    records: list[dict[str, object]] = []
    for raw_path in sorted({Path(path).resolve() for path in paths}, key=str):
        if not raw_path.exists() or not raw_path.is_file():
            raise FileNotFoundError(f"Fingerprint input is not a file: {raw_path}")
        try:
            relative = raw_path.relative_to(root).as_posix()
        except ValueError:
            relative = str(raw_path)
        records.append(
            {
                "path": relative,
                "size_bytes": raw_path.stat().st_size,
                "sha256": sha256_file(raw_path),
            }
        )

    canonical = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "file_count": len(records),
        "size_bytes": sum(int(row["size_bytes"]) for row in records),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": records,
    }


def companyfacts_paths(discovery: Path, cache_dir: Path) -> list[Path]:
    """Return CompanyFacts files referenced by usable discovery rows."""

    ciks: set[int] = set()
    with discovery.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("status", "")) != "new_filing_cached":
                continue
            raw_cik = str(row.get("cik", "")).strip()
            if raw_cik:
                ciks.add(int(float(raw_cik)))
    return [
        cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        for cik in sorted(ciks)
    ]


def git_provenance(repo_root: Path) -> dict[str, object]:
    """Record the exact commit and reject tracked source changes."""

    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    commit = run("rev-parse", "HEAD")
    dirty_output = run("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit,
        "tracked_worktree_clean": not bool(dirty_output),
    }


def build_research_input_fingerprints(
    *,
    repo_root: Path,
    discovery: Path,
    cache_dir: Path,
    historical_sec: Path,
    historical_panel: Path,
    pitindex_data: Path,
    market_snapshot: Path,
) -> dict[str, object]:
    """Build immutable fingerprints for every input consumed by the V2 run."""

    repo_root = repo_root.resolve()
    groups = {
        "discovery": fingerprint_files(root=repo_root, paths=[discovery]),
        "companyfacts": fingerprint_files(
            root=repo_root,
            paths=companyfacts_paths(discovery, cache_dir),
        ),
        "historical_sec": fingerprint_files(root=repo_root, paths=[historical_sec]),
        "historical_panel": fingerprint_files(
            root=repo_root, paths=[historical_panel]
        ),
        "pitindex": fingerprint_files(
            root=pitindex_data,
            paths=[
                pitindex_data / "sp500_seed.csv",
                pitindex_data / "sp500_changes.csv",
                pitindex_data / "sp500_current.csv",
            ],
        ),
        "market_snapshot": fingerprint_files(
            root=repo_root, paths=[market_snapshot]
        ),
    }
    provenance = git_provenance(repo_root)
    canonical = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "groups": groups,
        "code": provenance,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "bundle_sha256": digest}


def fingerprint_group_summary(bundle: dict[str, object]) -> dict[str, object]:
    groups = bundle.get("groups", {})
    return {
        name: {
            "file_count": value["file_count"],
            "size_bytes": value["size_bytes"],
            "sha256": value["sha256"],
        }
        for name, value in groups.items()
    }


def fingerprint_differences(
    expected: dict[str, object],
    actual: dict[str, object],
) -> list[str]:
    differences: list[str] = []
    if expected.get("schema_version") != actual.get("schema_version"):
        differences.append("schema_version")
    expected_groups = expected.get("groups", {})
    actual_groups = actual.get("groups", {})
    for name in sorted(set(expected_groups) | set(actual_groups)):
        left = expected_groups.get(name, {})
        right = actual_groups.get(name, {})
        if left.get("sha256") != right.get("sha256"):
            differences.append(name)
    if expected.get("code", {}).get("commit") != actual.get("code", {}).get("commit"):
        differences.append("code.commit")
    if not actual.get("code", {}).get("tracked_worktree_clean", False):
        differences.append("code.tracked_worktree_clean")
    return differences
