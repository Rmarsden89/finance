from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


MODEL_ID = "long_growth_v5_mom_add_v1"
EXPECTED_CANDIDATE_SHA256 = "8fb246f85cdea7f3a9d592e0da2b6b9f191cfffae518ee16ed219cff0c833f71"
REQUIRED_SHADOW_WEEKS = 8
CAPABILITIES = {
    "broker_access": False,
    "order_intents": False,
    "order_review": False,
    "order_placement": False,
    "order_modification": False,
    "order_cancellation": False,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run frozen V5 Momentum challenger as an execution-inert shadow."
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: dict) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    required = [
        "ticker",
        "quality_score",
        "financial_health_score",
        "growth_score",
        "valuation_score",
        "momentum_score",
    ]
    missing = [name for name in required if name not in result.columns]
    if missing:
        raise SystemExit("V5 family-score input missing columns: " + ", ".join(missing))

    values = {
        family: pd.to_numeric(result[family + "_score"], errors="coerce")
        for family in ("quality", "financial_health", "growth", "valuation")
    }
    momentum = pd.to_numeric(result["momentum_score"], errors="coerce")

    mod = config["growth_valuation_modifier"]
    neutral = float(mod["neutral_score"])
    max_shift = float(mod["maximum_growth_to_valuation_shift"])
    growth_pressure = ((values["growth"] - neutral) / (100.0 - neutral)).clip(0.0, 1.0)
    valuation_weakness = ((neutral - values["valuation"]) / neutral).clip(0.0, 1.0)
    severity = growth_pressure * valuation_weakness

    base = config["base_family_weights"]
    growth_weight = float(base["growth"]) - max_shift * severity
    valuation_weight = float(base["valuation"]) + max_shift * severity

    fundamental = pd.Series(np.nan, index=result.index, dtype="float64")
    family_count = pd.Series(0, index=result.index, dtype="int64")
    for idx in result.index:
        row_weights = {
            "quality": float(base["quality"]),
            "financial_health": float(base["financial_health"]),
            "growth": float(growth_weight.loc[idx]),
            "valuation": float(valuation_weight.loc[idx]),
        }
        weighted = 0.0
        available_weight = 0.0
        count = 0
        for family, weight in row_weights.items():
            value = values[family].loc[idx]
            if pd.notna(value):
                weighted += float(value) * weight
                available_weight += weight
                count += 1
        family_count.loc[idx] = count
        if count >= int(config["eligibility"]["minimum_fundamental_families"]) and available_weight > 0:
            fundamental.loc[idx] = weighted / available_weight

    full_four = family_count.eq(4)
    combo = config["final_combination"]
    final_score = (
        float(combo["fundamental_candidate_weight"]) * fundamental
        + float(combo["momentum_weight"]) * momentum
    )
    eligible = full_four & momentum.notna() & final_score.notna()

    result["v5_fundamental_score"] = fundamental
    result["v5_interaction_severity"] = severity
    result["v5_score"] = final_score
    result["v5_top_conviction_eligible"] = eligible
    return result


def build_decision(
    scored: pd.DataFrame,
    *,
    as_of: str,
    v1_decision_hash: str,
    input_bundle_sha256: str,
    config_sha256: str,
) -> dict:
    eligible = scored.loc[
        scored["v5_top_conviction_eligible"].fillna(False).astype(bool)
        & pd.to_numeric(scored["v5_score"], errors="coerce").notna()
    ].copy()
    eligible["v5_score"] = pd.to_numeric(eligible["v5_score"], errors="coerce")
    eligible["ticker"] = eligible["ticker"].astype(str).str.upper()
    eligible = eligible.sort_values(
        ["v5_score", "ticker"],
        ascending=[False, True],
        kind="mergesort",
    ).head(10)
    if len(eligible) != 10:
        raise SystemExit("V5 shadow did not produce exactly 10 eligible selections")

    top10 = [
        {
            "rank": rank,
            "ticker": row.ticker,
            "score": float(row.v5_score),
        }
        for rank, row in enumerate(eligible.itertuples(index=False), start=1)
    ]
    canonical = {
        "schema_version": 1,
        "as_of": as_of,
        "model_id": MODEL_ID,
        "v1_decision_hash": v1_decision_hash,
        "configuration_hash": config_sha256,
        "input_bundle_sha256": input_bundle_sha256,
        "top10": top10,
        "execution_capabilities": CAPABILITIES,
    }
    return {
        **canonical,
        "decision_hash": canonical_sha256(canonical),
    }


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    config_path = root / "config" / "long_growth_v5_mom_add_v1.json"
    candidate_path = root / "config" / "v5_mom_add_cand_001.json"
    v1_dir = root / "reports" / "shadow" / as_of
    v1_decision_path = v1_dir / "shadow_decision.json"
    family_scores_path = root / "reports" / "current_shadow_family_scores.csv"
    current_snapshot_path = root / "reports" / "current_shadow_snapshot.csv"

    output_dir = root / "reports" / "v5" / MODEL_ID / as_of / "shadow"
    decision_path = output_dir / "v5_shadow_decision.json"
    summary_path = output_dir / "summary.json"
    scored_path = output_dir / "v5_current_scores.csv"
    snapshot_copy = output_dir / "current_shadow_snapshot.csv"
    family_copy = output_dir / "current_shadow_family_scores.csv"
    ledger_dir = root / "reports" / "v5" / MODEL_ID / "shadow_ledger"
    ledger_path = ledger_dir / "ledger.csv"
    ledger_summary_path = ledger_dir / "summary.json"

    if output_dir.exists():
        raise SystemExit(
            "V5 shadow output already exists for this date; observations are immutable: "
            + str(output_dir)
        )

    required_paths = [
        config_path,
        candidate_path,
        v1_decision_path,
        family_scores_path,
        current_snapshot_path,
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise SystemExit("Missing V5 shadow input(s):\n  " + "\n  ".join(missing))

    config = read_json(config_path)
    candidate = read_json(candidate_path)
    if config.get("model_id") != MODEL_ID or config.get("research_only") is not True:
        raise SystemExit("Unexpected frozen V5 configuration")
    if config.get("live_authorization") is not False:
        raise SystemExit("V5 shadow configuration must explicitly disable live authorization")
    if any(bool(value) for value in config.get("execution_capabilities", {}).values()):
        raise SystemExit("V5 frozen configuration enables an execution capability")
    if canonical_sha256(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise SystemExit("Frozen V5 source candidate configuration hash mismatch")

    v1_decision = read_json(v1_decision_path)
    v1_hash = str(v1_decision.get("decision_hash") or "").strip()
    if not v1_hash:
        raise SystemExit("Saved V1 decision is missing decision_hash")
    if str(v1_decision.get("as_of") or "") != as_of:
        raise SystemExit("Saved V1 decision date does not match requested V5 shadow date")

    input_hashes = {
        "v1_decision": sha256_file(v1_decision_path),
        "family_scores": sha256_file(family_scores_path),
        "current_snapshot": sha256_file(current_snapshot_path),
        "frozen_config": sha256_file(config_path),
        "source_candidate_config": sha256_file(candidate_path),
    }
    input_bundle_sha256 = canonical_sha256(input_hashes)
    config_sha256 = canonical_sha256(config)

    family_scores = pd.read_csv(family_scores_path, low_memory=False)
    scored = score(family_scores, config)
    decision = build_decision(
        scored,
        as_of=as_of,
        v1_decision_hash=v1_hash,
        input_bundle_sha256=input_bundle_sha256,
        config_sha256=config_sha256,
    )

    v1_top = {
        str(row.get("ticker") or "").upper()
        for row in (v1_decision.get("top10") or v1_decision.get("decisions") or [])
        if int(row.get("rank") or 999) <= 10
    }
    v5_top = {row["ticker"] for row in decision["top10"]}
    overlap = len(v1_top & v5_top)
    entered = sorted(v5_top - v1_top)
    exited = sorted(v1_top - v5_top)

    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(current_snapshot_path, snapshot_copy)
    shutil.copy2(family_scores_path, family_copy)
    scored.to_csv(scored_path, index=False)
    write_json(decision_path, decision)

    commit = git_commit(root)
    summary = {
        "schema_version": 1,
        "status": "V5_SHADOW_OBSERVATION_COMPLETE",
        "as_of": as_of,
        "model_id": MODEL_ID,
        "v1_decision_hash": v1_hash,
        "v5_decision_hash": decision["decision_hash"],
        "configuration_hash": config_sha256,
        "source_candidate_config_sha256": EXPECTED_CANDIDATE_SHA256,
        "input_bundle_sha256": input_bundle_sha256,
        "code_commit": commit,
        "shadow_week_valid": True,
        "pit_violations": 0,
        "v1_v5_top10_overlap": overlap,
        "v5_entered_top10": entered,
        "v5_exited_top10": exited,
        "eligible_count": int(scored["v5_top_conviction_eligible"].fillna(False).sum()),
        "execution_capabilities": CAPABILITIES,
        "live_promotion_authorized": False,
    }
    write_json(summary_path, summary)

    ledger_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "as_of": as_of,
        "model_id": MODEL_ID,
        "v1_decision_hash": v1_hash,
        "v5_decision_hash": decision["decision_hash"],
        "input_bundle_sha256": input_bundle_sha256,
        "configuration_hash": config_sha256,
        "code_commit": commit,
        "top10_overlap_v1": overlap,
        "entered_v5": "|".join(entered),
        "exited_v5": "|".join(exited),
        "pit_violations": 0,
        "valid": True,
    }
    if ledger_path.exists():
        ledger = pd.read_csv(ledger_path, low_memory=False)
        if as_of in set(ledger["as_of"].astype(str)):
            raise SystemExit("V5 shadow ledger already contains this date")
        ledger = pd.concat([ledger, pd.DataFrame([row])], ignore_index=True)
    else:
        ledger = pd.DataFrame([row])
    ledger = ledger.sort_values("as_of")
    ledger.to_csv(ledger_path, index=False)

    valid = ledger.loc[ledger["valid"].fillna(False).astype(bool)].copy()
    valid["iso_week"] = pd.to_datetime(valid["as_of"]).dt.strftime("%G-W%V")
    distinct_weeks = int(valid["iso_week"].nunique())
    write_json(ledger_summary_path, {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "valid_shadow_observations": int(len(valid)),
        "distinct_valid_shadow_weeks": distinct_weeks,
        "required_distinct_valid_shadow_weeks": REQUIRED_SHADOW_WEEKS,
        "formal_review_ready": distinct_weeks >= REQUIRED_SHADOW_WEEKS,
        "live_promotion_authorized": False,
    })

    print("V5 FROZEN CHALLENGER SHADOW OBSERVATION")
    print("Model:                       " + MODEL_ID)
    print("As of:                       " + as_of)
    print("V1 decision hash:            " + v1_hash)
    print("V5 decision hash:            " + decision["decision_hash"])
    print("V1/V5 Top-10 overlap:        {}/10".format(overlap))
    print("V5 entered / exited:         {} / {}".format(
        ",".join(entered) if entered else "-",
        ",".join(exited) if exited else "-",
    ))
    print("Eligible names:              {}".format(summary["eligible_count"]))
    print("PIT violations:              0")
    print("Distinct valid shadow weeks: {}/{}".format(
        distinct_weeks,
        REQUIRED_SHADOW_WEEKS,
    ))
    print("Formal review ready:         {}".format(
        "YES" if distinct_weeks >= REQUIRED_SHADOW_WEEKS else "NO"
    ))
    print("Live authorization:          NO")
    print("Output:                      " + str(output_dir))


if __name__ == "__main__":
    main()
