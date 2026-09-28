from __future__ import annotations

"""Read-only, registry-driven comparison of saved V1 and shadow decisions.

The 8-week gate enables an informational review. It never authorizes trading,
changes a frozen model, or invents realized/paper performance data.
"""

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any


REVIEW_WEEKS = 8


def read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return result


def decision_top10(decision: dict[str, Any], *, source: Path) -> list[dict[str, Any]]:
    rows = decision.get("top10")
    if rows is None:
        rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError(f"Missing selection list in {source}")
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Malformed selection in {source}")
        rank = int(row["rank"])
        if rank > 10:
            continue
        ticker = str(row["ticker"]).strip().upper()
        if rank < 1 or not ticker:
            raise ValueError(f"Invalid rank or ticker in {source}")
        output.append({"rank": rank, "ticker": ticker, "score": row.get("score")})
    output.sort(key=lambda row: row["rank"])
    ranks = [row["rank"] for row in output]
    tickers = [row["ticker"] for row in output]
    if ranks != list(range(1, 11)) or len(set(tickers)) != 10:
        raise ValueError(f"Expected unique ranks/tickers 1..10 in {source}")
    return output


def get_registry(root: Path, registry_path: Path) -> list[dict[str, Any]]:
    source = registry_path if registry_path.is_absolute() else root / registry_path
    registry = read_json(source)
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported shadow registry schema version")
    modes = registry.get("modes")
    if not isinstance(modes, list):
        raise ValueError("Shadow registry modes must be a list")
    names: set[str] = set()
    for mode in modes:
        if not isinstance(mode, dict):
            raise ValueError("Malformed shadow registry entry")
        for key in ("id", "summary", "decision", "expected_status"):
            if not isinstance(mode.get(key), str) or not mode[key].strip():
                raise ValueError(f"Shadow mode is missing {key}")
        if mode["id"] == "v1" or mode["id"] in names:
            raise ValueError("Duplicate or reserved shadow model identifier")
        names.add(mode["id"])
    return modes


def render_path(root: Path, template: str, as_of: str) -> Path:
    candidate = Path(template.format(as_of=as_of))
    return candidate if candidate.is_absolute() else root / candidate


def validate_shadow(
    root: Path,
    mode: dict[str, Any],
    *,
    as_of: str,
    v1_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    summary_path = render_path(root, mode["summary"], as_of)
    decision_path = render_path(root, mode["decision"], as_of)
    if not summary_path.exists() and not decision_path.exists():
        return None
    if not summary_path.exists() or not decision_path.exists():
        raise ValueError(f"Incomplete {mode['id']} artifacts for {as_of}")
    summary = read_json(summary_path)
    decision = read_json(decision_path)
    if summary.get("status") != mode["expected_status"]:
        raise ValueError(f"Invalid {mode['id']} completion status for {as_of}")
    if summary.get("as_of") != as_of or decision.get("as_of") != as_of:
        raise ValueError(f"Decision date mismatch for {mode['id']} {as_of}")
    if summary.get("shadow_week_valid") is not True:
        raise ValueError(f"Invalid shadow week for {mode['id']} {as_of}")
    if summary.get("pit_violations") != 0:
        raise ValueError(f"PIT violation for {mode['id']} {as_of}")
    if summary.get("v1_decision_hash") != v1_hash or decision.get("v1_decision_hash") != v1_hash:
        raise ValueError(f"V1 decision hash mismatch for {mode['id']} {as_of}")
    capabilities = summary.get("execution_capabilities")
    if not isinstance(capabilities, dict) or any(capabilities.values()):
        raise ValueError(f"Execution-capability violation in {mode['id']} {as_of}")
    if decision.get("execution_capabilities") != capabilities:
        raise ValueError(f"Execution-capability mismatch in {mode['id']} {as_of}")
    decision_hash = str(decision.get("decision_hash") or "")
    expected_key = f"{mode['id'].split('_')[0]}_decision_hash"
    if not decision_hash or summary.get(expected_key) != decision_hash:
        # Future modes may declare a custom summary hash field.
        hash_field = mode.get("decision_hash_field", expected_key)
        if not decision_hash or summary.get(hash_field) != decision_hash:
            raise ValueError(f"Decision hash mismatch for {mode['id']} {as_of}")
    return summary, decision_top10(decision, source=decision_path)


def iso_week(date_string: str) -> str:
    year, week, _ = date.fromisoformat(date_string).isocalendar()
    return f"{year}-W{week:02d}"


def build_comparison(root: Path, modes: list[dict[str, Any]]) -> tuple[list[dict], list[dict], dict]:
    run_root = root / "reports" / "shadow"
    observations: list[dict] = []
    selections: list[dict] = []
    comparable: dict[str, dict[str, set[str]]] = {}
    model_weeks: dict[str, set[str]] = {mode["id"]: set() for mode in modes}
    model_dates: dict[str, set[str]] = {mode["id"]: set() for mode in modes}
    model_weeks["v1"] = set()
    model_dates["v1"] = set()
    failures: list[dict] = []

    if not run_root.exists():
        raise ValueError(f"Live run root not found: {run_root}")
    for run_dir in sorted(run_root.iterdir()):
        if not run_dir.is_dir():
            continue
        try:
            as_of = date.fromisoformat(run_dir.name).isoformat()
        except ValueError:
            continue
        state_path = run_dir / "workflow_state.json"
        if not state_path.exists() or read_json(state_path).get("status") != "COMPLETE":
            continue
        v1_path = run_dir / "shadow_decision.json"
        if not v1_path.exists():
            raise ValueError(f"Completed V1 missing decision: {v1_path}")
        v1_decision = read_json(v1_path)
        v1_hash = str(v1_decision.get("decision_hash") or "")
        if not v1_hash:
            raise ValueError(f"Missing V1 decision hash: {v1_path}")
        v1_top10 = decision_top10(v1_decision, source=v1_path)
        week = iso_week(as_of)

        sets: dict[str, set[str]] = {"v1": {row["ticker"] for row in v1_top10}}
        observations.append({
            "as_of": as_of, "iso_week": week, "model_id": "v1",
            "decision_hash": v1_hash, "v1_decision_hash": v1_hash,
            "pit_violations": 0, "status": "COMPLETE", "top10_overlap_v1": 10,
        })
        model_weeks["v1"].add(week)
        model_dates["v1"].add(as_of)
        for row in v1_top10:
            selections.append({"as_of": as_of, "iso_week": week, "model_id": "v1",
                "decision_hash": v1_hash, **row})
        for mode in modes:
            try:
                shadow = validate_shadow(root, mode, as_of=as_of, v1_hash=v1_hash)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                failures.append({"as_of": as_of, "model_id": mode["id"], "reason": str(exc)})
                continue
            if shadow is None:
                continue
            summary, top10 = shadow
            selected = {row["ticker"] for row in top10}
            sets[mode["id"]] = selected
            model_weeks[mode["id"]].add(week)
            model_dates[mode["id"]].add(as_of)
            observations.append({
                "as_of": as_of, "iso_week": week, "model_id": mode["id"],
                "decision_hash": summary.get(mode.get("decision_hash_field", f"{mode['id'].split('_')[0]}_decision_hash")),
                "v1_decision_hash": v1_hash, "pit_violations": 0,
                "status": summary["status"],
                "top10_overlap_v1": len(selected & sets["v1"]),
            })
            for row in top10:
                selections.append({
                    "as_of": as_of, "iso_week": week, "model_id": mode["id"],
                    "decision_hash": observations[-1]["decision_hash"], **row,
                })
        comparable[as_of] = sets

    pairwise: list[dict] = []
    for as_of, sets in sorted(comparable.items()):
        names = sorted(sets)
        for idx, left in enumerate(names):
            for right in names[idx + 1:]:
                pairwise.append({
                    "as_of": as_of, "iso_week": iso_week(as_of),
                    "model_a": left, "model_b": right,
                    "overlap": len(sets[left] & sets[right]),
                    "only_a": "|".join(sorted(sets[left] - sets[right])),
                    "only_b": "|".join(sorted(sets[right] - sets[left])),
                })
    summary = {
        "schema_version": 1,
        "review_rule": "8_distinct_iso_calendar_weeks_per_model",
        "required_weeks": REVIEW_WEEKS,
        "models": {
            name: {
                "valid_observation_dates": len(model_dates[name]),
                "distinct_qualifying_weeks": len(model_weeks[name]),
                "formal_review_ready": len(model_weeks[name]) >= REVIEW_WEEKS,
                "qualifying_weeks": sorted(model_weeks[name]),
            } for name in model_weeks
        },
        "invalid_or_incomplete_shadow_artifacts": failures,
        "selection_return_status": "not_calculated_by_this_decision_comparator",
        "virtual_portfolio_status": "not_calculated_by_this_decision_comparator",
        "live_promotion_authorized": False,
    }
    return observations, selections, {"summary": summary, "pairwise": pairwise}


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only model-agnostic decision comparisons")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--registry", type=Path, default=Path("config/live_shadow_modes.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/model_comparison"))
    args = parser.parse_args()
    root = args.repo_root.resolve()
    modes = get_registry(root, args.registry)
    observations, selections, data = build_comparison(root, modes)
    out_dir = render_path(root, str(args.output_dir), "")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "observations.csv", observations,
        ["as_of", "iso_week", "model_id", "decision_hash",
         "v1_decision_hash", "pit_violations", "status", "top10_overlap_v1"])
    write_csv(out_dir / "selections.csv", selections,
        ["as_of", "iso_week", "model_id", "decision_hash", "rank", "ticker", "score"])
    write_csv(out_dir / "pairwise_overlap.csv", data["pairwise"],
        ["as_of", "iso_week", "model_a", "model_b", "overlap", "only_a", "only_b"])
    (out_dir / "summary.json").write_text(
        json.dumps(data["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("READ-ONLY MULTI-MODEL DECISION COMPARISON")
    for model, metrics in sorted(data["summary"]["models"].items()):
        print(
            f"{model}: {metrics['valid_observation_dates']} observation(s), "
            f"{metrics['distinct_qualifying_weeks']}/{REVIEW_WEEKS} distinct ISO weeks; "
            f"formal review ready={metrics['formal_review_ready']}"
        )
    print(f"Output: {out_dir}")
    print("No return assumptions, shadow orders, or model promotion were made.")


if __name__ == "__main__":
    main()
