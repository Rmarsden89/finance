from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from finance.data.current_shadow_panel import (
    assemble_current_scoring_panel,
    build_current_shadow_row,
    build_sec_only_weekly_extension,
)
from finance.data.sec_shadow_merge import merge_current_sec_shadow
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    resolve_v2_impact_artifact_paths,
    resolve_v2_sec_artifact_paths,
)
from finance.research.v2_impact import compare_v1_v2_impact, score_long_growth_panel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the frozen exact-only V1 data policy with the completed "
            "V2 DEI-fallback challenger using identical saved inputs. No "
            "broker or order capability exists in this command."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/sec/current"))
    parser.add_argument(
        "--historical-sec",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument(
        "--historical-panel",
        type=Path,
        default=Path("reports/weekly_research_panel_2015_2025.csv"),
    )
    parser.add_argument(
        "--v1-decision",
        type=Path,
        help=(
            "Saved V1 shadow_decision.json used for champion regression. "
            "Default: reports/shadow/<as-of>/shadow_decision.json."
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _run_baseline_candidates(
    *, repo_root: Path, discovery: Path, cache_dir: Path, output: Path, audit: Path
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "build_sec_current_candidates.py"),
            "--discovery", str(discovery),
            "--cache-dir", str(cache_dir),
            "--output", str(output),
            "--audit-output", str(audit),
            "--share-fallback-policy", "v1_exact_only",
        ],
        cwd=repo_root,
        check=True,
    )


def main() -> None:
    args = parse_args()
    config = LONG_GROWTH_V2_RESEARCH
    config.validate()
    repo_root = args.repo_root.resolve()
    impact = resolve_v2_impact_artifact_paths(repo_root, args.as_of, config=config)
    v2 = resolve_v2_sec_artifact_paths(repo_root, args.as_of, config=config)
    impact["baseline_dir"].mkdir(parents=True, exist_ok=True)
    impact["challenger_dir"].mkdir(parents=True, exist_ok=True)

    manifest_path = v2["manifest"]
    if not manifest_path.exists():
        raise SystemExit(f"Missing completed V2 manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 manifest must have status SEC_RESEARCH_COMPLETE")
    capabilities = manifest.get("execution_capabilities", {})
    if any(bool(value) for value in capabilities.values()):
        raise SystemExit("V2 manifest enables an execution capability")

    discovery = _resolve(repo_root, args.discovery)
    cache_dir = _resolve(repo_root, args.cache_dir)
    historical_sec = _resolve(repo_root, args.historical_sec)
    historical_panel = _resolve(repo_root, args.historical_panel)
    pitindex_data = _resolve(repo_root, args.pitindex_data)
    v1_decision = _resolve(
        repo_root,
        args.v1_decision
        or Path("reports") / "shadow" / args.as_of.isoformat() / "shadow_decision.json",
    )
    required = {
        "discovery": discovery,
        "SEC cache": cache_dir,
        "historical SEC": historical_sec,
        "historical panel": historical_panel,
        "PITIndex": pitindex_data,
        "V2 snapshot": v2["current_snapshot"],
        "V2 scoring panel": v2["scoring_panel"],
        "saved V1 decision": v1_decision,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing required comparison input(s):\n  " + "\n  ".join(missing))

    print("V1 / V2 SAME-INPUT IMPACT BUILD", flush=True)
    print(f"As of:                      {args.as_of.isoformat()}", flush=True)
    print(f"Output directory:           {impact['impact_dir']}", flush=True)
    print("Execution capabilities:     disabled", flush=True)

    _run_baseline_candidates(
        repo_root=repo_root,
        discovery=discovery,
        cache_dir=cache_dir,
        output=impact["baseline_candidates"],
        audit=impact["baseline_candidate_audit"],
    )

    historical = pd.read_csv(historical_sec, low_memory=False)
    exact_candidates = pd.read_csv(impact["baseline_candidates"], low_memory=False)
    baseline_shadow, merge_audit, merge_summary = merge_current_sec_shadow(
        historical,
        exact_candidates,
        as_of=pd.Timestamp(args.as_of),
    )
    baseline_shadow.to_csv(impact["baseline_sec_shadow"], index=False)
    merge_audit.to_csv(impact["baseline_merge_audit"], index=False)
    pd.DataFrame([asdict(merge_summary)]).to_csv(
        impact["baseline_merge_summary"], index=False
    )

    intervals = load_pitindex_sp500(pitindex_data)
    input_artifacts = manifest.get("input_artifacts", {})
    market_value = input_artifacts.get("normalized market snapshot")
    if not market_value:
        raise SystemExit("Completed V2 manifest lacks normalized market snapshot input")
    market_path = Path(market_value)
    if not market_path.exists():
        raise SystemExit(f"Saved market snapshot not found: {market_path}")
    market = pd.read_csv(market_path, low_memory=False)
    baseline_snapshot = build_current_shadow_row(
        intervals, baseline_shadow, market, as_of=pd.Timestamp(args.as_of)
    )
    last_friday = pd.Timestamp(args.as_of) - pd.offsets.Week(weekday=4)
    if last_friday.date() == args.as_of:
        last_friday -= pd.Timedelta(days=7)
    baseline_extension = build_sec_only_weekly_extension(
        intervals,
        baseline_shadow,
        start=pd.Timestamp("2026-01-02"),
        end=last_friday,
    )
    historical_frame = pd.read_csv(historical_panel, low_memory=False)
    baseline_panel = assemble_current_scoring_panel(
        historical_frame, baseline_extension, baseline_snapshot
    )
    baseline_snapshot.to_csv(impact["baseline_current_snapshot"], index=False)
    baseline_panel.to_csv(impact["baseline_scoring_panel"], index=False)

    challenger_snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    challenger_panel = pd.read_csv(v2["scoring_panel"], low_memory=False)
    baseline_scored = score_long_growth_panel(baseline_panel)
    challenger_scored = score_long_growth_panel(challenger_panel)
    baseline_scored.to_csv(impact["baseline_scored"], index=False)
    challenger_scored.to_csv(impact["challenger_scored"], index=False)

    detail, top10, pit_audit, summary = compare_v1_v2_impact(
        baseline_snapshot=baseline_snapshot,
        challenger_snapshot=challenger_snapshot,
        baseline_scored=baseline_scored,
        challenger_scored=challenger_scored,
        as_of=pd.Timestamp(args.as_of),
    )
    detail.to_csv(impact["ticker_detail"], index=False)
    top10.to_csv(impact["top10_comparison"], index=False)
    pit_audit.to_csv(impact["pit_audit"], index=False)
    champion = json.loads(v1_decision.read_text(encoding="utf-8"))
    champion_rows = sorted(champion.get("decisions", []), key=lambda row: row["rank"])
    baseline_rows = top10.loc[top10["baseline_top10"].fillna(False).astype(bool)].copy()
    baseline_rows = baseline_rows.sort_values("baseline_selection_rank", kind="stable")
    baseline_lookup = baseline_rows.set_index("ticker")
    regression_rows = []
    for row in champion_rows:
        ticker = str(row.get("ticker", "")).upper()
        baseline = baseline_lookup.loc[ticker] if ticker in baseline_lookup.index else None
        baseline_rank = None if baseline is None else int(baseline["baseline_selection_rank"])
        baseline_score = None if baseline is None else float(
            baseline["baseline_long_growth_v1_score"]
        )
        champion_score = float(row["score"])
        regression_rows.append({
            "champion_rank": int(row["rank"]),
            "ticker": ticker,
            "champion_score": champion_score,
            "baseline_rank": baseline_rank,
            "baseline_score": baseline_score,
            "rank_matches": baseline_rank == int(row["rank"]),
            "score_matches": (
                baseline_score is not None
                and abs(baseline_score - champion_score) <= 1e-9
            ),
        })
    regression = pd.DataFrame(regression_rows)
    regression.to_csv(impact["champion_regression"], index=False)
    champion_order_match = bool(
        len(regression) == 10
        and regression["rank_matches"].fillna(False).all()
    )
    champion_scores_match = bool(
        len(regression) == 10
        and regression["score_matches"].fillna(False).all()
    )
    decision_hash = str(champion.get("decision_hash", ""))
    baseline_hash_match = decision_hash != "" and decision_hash in str(
        manifest.get("data_baseline_id", "")
    )
    summary_payload = asdict(summary)
    summary_payload.update({
        "status": "IMPACT_COMPARISON_COMPLETE",
        "decision_date": args.as_of.isoformat(),
        "data_baseline_id": manifest.get("data_baseline_id"),
        "baseline_policy": "v1_exact_only",
        "challenger_policy": "v2_dei_cover_date",
        "execution_capabilities": capabilities,
        "champion_decision_hash": decision_hash,
        "data_baseline_decision_hash_match": baseline_hash_match,
        "champion_top10_order_match": champion_order_match,
        "champion_top10_scores_match": champion_scores_match,
    })
    pd.DataFrame([summary_payload]).to_csv(impact["summary_csv"], index=False)
    impact["summary_json"].write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest["impact_comparison"] = {
        "status": "IMPACT_COMPARISON_COMPLETE",
        "summary": str(impact["summary_json"]),
        "ticker_detail": str(impact["ticker_detail"]),
        "top10_comparison": str(impact["top10_comparison"]),
        "champion_regression": str(impact["champion_regression"]),
        "pit_audit": str(impact["pit_audit"]),
    }
    stages = list(manifest.get("allowed_stages", []))
    if "same_input_impact_comparison" not in stages:
        stages.append("same_input_impact_comparison")
    manifest["allowed_stages"] = stages
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V1 / V2 IMPACT COMPARISON COMPLETE", flush=True)
    print(
        f"Shares coverage:            {summary.baseline_shares_present}/"
        f"{summary.universe_rows} -> {summary.challenger_shares_present}/"
        f"{summary.universe_rows}", flush=True
    )
    print(
        f"Valuation availability:     {summary.baseline_valuation_available} -> "
        f"{summary.challenger_valuation_available}", flush=True
    )
    print(
        f"Top-conviction eligible:    {summary.baseline_top_conviction_eligible} -> "
        f"{summary.challenger_top_conviction_eligible}", flush=True
    )
    print(f"Top-10 overlap:             {summary.top10_overlap}/10", flush=True)
    print(f"Champion Top-10 order:      {'MATCH' if champion_order_match else 'DIFFERS'}", flush=True)
    print(f"Champion scores:            {'MATCH' if champion_scores_match else 'DIFFER'}", flush=True)
    print(f"PIT violations:             {summary.pit_violations}", flush=True)
    print(f"Summary:                    {impact['summary_json']}", flush=True)
    print(f"Ticker detail:              {impact['ticker_detail']}", flush=True)
    print("V1 reports and SEC shadow cache were NOT modified.", flush=True)
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.", flush=True)


if __name__ == "__main__":
    main()
