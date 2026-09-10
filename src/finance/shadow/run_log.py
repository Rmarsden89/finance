from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str | None:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    return {
        "path": str(target),
        "exists": target.exists(),
        "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size if target.exists() else None,
    }


def append_run_event(path: str | Path, event: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


@contextmanager
def logged_stage(
    log_path: str | Path,
    stage: str,
    *,
    inputs: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    started_at = utc_now_iso()
    details: dict[str, Any] = {}
    try:
        yield details
    except Exception as exc:
        append_run_event(
            log_path,
            {
                "stage": stage,
                "status": "failed",
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "inputs": inputs or {},
                "details": details,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    else:
        append_run_event(
            log_path,
            {
                "stage": stage,
                "status": "success",
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "inputs": inputs or {},
                "details": details,
            },
        )
