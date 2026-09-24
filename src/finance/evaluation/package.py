from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


class EvaluationPackageError(RuntimeError):
    pass


SCHEMA_VERSION = "1.1"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationPackageError(f"Missing required artifact: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path)}


def _broker_cash(path: Path) -> float:
    payload = _read_json(path)
    portfolio = payload.get("portfolio")
    if isinstance(portfolio, dict) and portfolio.get("cash") not in (None, ""):
        return float(portfolio["cash"])

    response = (
        payload.get("raw_responses", {})
        .get("portfolio", {})
        .get("response", {})
    )
    structured = (
        response.get("structuredContent")
        or response.get("structured_content")
        or {}
    )
    data = structured.get("data") if isinstance(structured, dict) else None
    if isinstance(data, dict) and data.get("cash") not in (None, ""):
        return float(data["cash"])

    raise EvaluationPackageError(f"Broker snapshot is missing portfolio cash: {path}")


def _portfolio_summary(path: Path) -> tuple[float, int]:
    if not path.exists():
        raise EvaluationPackageError(f"Missing portfolio state: {path}")
    frame = pd.read_csv(path)
    if not {"ticker", "market_value"}.issubset(frame.columns):
        raise EvaluationPackageError(f"Malformed portfolio state: {path}")
    values = pd.to_numeric(frame["market_value"], errors="coerce")
    if values.isna().any() or (values < 0).any():
        raise EvaluationPackageError(f"Invalid portfolio market values: {path}")
    return float(values.sum()), int(len(frame))


def _submission_matches(run_dir: Path) -> dict[str, dict]:
    for path in (run_dir / "submission_reconciliation_postfill.json", run_dir / "submission_reconciliation.json"):
        if path.exists():
            payload = _read_json(path)
            return {str(row.get("ticker") or "").upper(): row for row in (payload.get("matches") or []) if isinstance(row, dict) and row.get("ticker")}
    return {}


def build_v1_evaluation_package(run_dir: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    try:
        run_date = date.fromisoformat(run_dir.name)
    except ValueError as exc:
        raise EvaluationPackageError(f"Run directory must be named YYYY-MM-DD: {run_dir}") from exc

    required = {
        "workflow_state": run_dir / "workflow_state.json",
        "shadow_decision": run_dir / "shadow_decision.json",
        "post_fill_reconciliation": run_dir / "post_fill_reconciliation.json",
        "portfolio_state": run_dir / "portfolio_state.csv",
        "broker_snapshot_presubmit": run_dir / "broker_snapshot_presubmit.json",
        "broker_snapshot_postfill": run_dir / "broker_snapshot_postfill.json",
    }
    for path in required.values():
        if not path.exists():
            raise EvaluationPackageError(f"Missing required artifact: {path}")

    state = _read_json(required["workflow_state"])
    if state.get("status") != "COMPLETE":
        raise EvaluationPackageError(f"Run is not COMPLETE: status={state.get('status')!r}")

    decision = _read_json(required["shadow_decision"])
    if str(decision.get("model_id") or "") != "long_growth_v1":
        raise EvaluationPackageError(f"Unexpected model_id: {decision.get('model_id')!r}")

    post = _read_json(required["post_fill_reconciliation"])
    if not post.get("portfolio_state_ready"):
        raise EvaluationPackageError("Post-fill portfolio is not ready")

    cash_change = post.get("cash_change")
    if cash_change in (None, ""):
        raise EvaluationPackageError("Post-fill reconciliation is missing cash_change")
    deployed = -float(cash_change)
    if deployed <= 0:
        raise EvaluationPackageError(f"Expected positive deployed contribution, got {deployed}")

    planned = float(decision.get("planned_investment") or 0.0)
    if abs(planned - deployed) > 0.02:
        raise EvaluationPackageError(f"Planned/deployed mismatch: planned={planned:.2f}, deployed={deployed:.2f}")

    position_value, position_count = _portfolio_summary(required["portfolio_state"])
    account_cash_presubmit = _broker_cash(required["broker_snapshot_presubmit"])
    account_cash_postfill = _broker_cash(required["broker_snapshot_postfill"])
    if abs((account_cash_postfill - account_cash_presubmit) + deployed) > 0.02:
        raise EvaluationPackageError(
            "Broker cash movement does not reconcile to deployed contribution: "
            f"pre={account_cash_presubmit:.2f}, post={account_cash_postfill:.2f}, "
            f"deployed={deployed:.2f}"
        )

    matches = _submission_matches(run_dir)
    selections: list[dict[str, Any]] = []
    for row in decision.get("decisions") or []:
        if str(row.get("status") or "") != "buy":
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        match = matches.get(ticker) or {}
        selections.append({
            "ticker": ticker,
            "rank": int(row.get("rank")),
            "score": float(row.get("score")),
            "allocation_dollars": float(row.get("allocation_dollars") or 0.0),
            "broker_order_id": match.get("broker_order_id"),
            "filled_quantity": match.get("filled_quantity"),
            "average_price": match.get("average_price"),
            "submitted_at": match.get("submitted_at"),
        })

    benchmark_path = run_dir / "benchmark_spy_capture.json"
    benchmark_capture = _read_json(benchmark_path) if benchmark_path.exists() else None
    artifact_paths = {
        **required,
        "submission_reconciliation_postfill": run_dir / "submission_reconciliation_postfill.json",
        "benchmark_spy_capture": benchmark_path,
    }
    artifacts = {name: _artifact(path) for name, path in artifact_paths.items() if path.exists()}

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "broker_order_capability": False,
        "run_date": run_date.isoformat(),
        "run_id": state.get("run_id"),
        "model_id": "long_growth_v1",
        "decision_hash": str(decision.get("decision_hash") or ""),
        "deployed_contribution": deployed,
        "planned_investment": planned,
        "postfill_position_value": position_value,
        "postfill_position_count": position_count,
        "account_cash_presubmit": account_cash_presubmit,
        "account_cash_postfill": account_cash_postfill,
        "cash_movement_reconciled": True,
        "postfill_reconciled": bool(post.get("reconciled")),
        "portfolio_state_ready": bool(post.get("portfolio_state_ready")),
        "selection_count": len(selections),
        "selections": selections,
        "benchmark": {
            "symbol": "SPY",
            "capture": benchmark_capture,
            "status": "captured" if benchmark_capture is not None else "missing_historical_capture",
        },
        "artifacts": artifacts,
    }
