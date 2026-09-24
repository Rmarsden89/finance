from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.ttm_shadow_evaluation import (
    build_period_summary,
    build_ticker_event_detail,
    render_evaluation_markdown,
    weekly_coverage,
)
from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    V2_ARTIFACT_ROOT,
    resolve_v2_shadow_artifact_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the deterministic Issue #24 cumulative evaluation package "
            "from existing Issue #18 V2 shadow artifacts."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    model_root = (
        root
        / V2_ARTIFACT_ROOT
        / LONG_GROWTH_V2_RESEARCH.model_id
    )
    ledger_dir = model_root / "shadow_ledger"
    ledger_path = ledger_dir / "shadow_ledger.csv"
    if not ledger_path.exists():
        raise SystemExit(f"Missing shadow ledger: {ledger_path}")

    ledger = pd.read_csv(ledger_path, low_memory=False)
    if ledger.empty:
        raise SystemExit("Shadow ledger is empty")
    if "as_of" not in ledger.columns or "valid" not in ledger.columns:
        raise SystemExit("Shadow ledger is missing required as_of/valid columns")

    valid = ledger["valid"].fillna(False).astype(bool)
    valid_ledger = ledger.loc[valid].copy()
    if valid_ledger.empty:
        raise SystemExit("Shadow ledger contains no valid observations")

    weekly_rows: list[dict[str, object]] = []
    comparisons: list[tuple[str, pd.DataFrame]] = []

    for row in valid_ledger.sort_values("as_of", kind="stable").itertuples(index=False):
        as_of_text = str(row.as_of)
        as_of = date.fromisoformat(as_of_text)
        paths = resolve_v2_shadow_artifact_paths(root, as_of)

        required = {
            "summary": paths["summary"],
            "decision": paths["decision"],
            "fingerprints": paths["input_fingerprints"],
            "current_scores": paths["v2_current_scores"],
            "comparison": paths["comparison"],
        }
        missing = [
            f"{name}: {path}"
            for name, path in required.items()
            if not path.exists()
        ]
        if missing:
            raise SystemExit(
                f"Missing shadow artifact(s) for {as_of_text}:\n  "
                + "\n  ".join(missing)
            )

        summary = _read_json(paths["summary"])
        decision = _read_json(paths["decision"])
        fingerprints = _read_json(paths["input_fingerprints"])
        current = pd.read_csv(paths["v2_current_scores"], low_memory=False)
        comparison = pd.read_csv(paths["comparison"], low_memory=False)
        comparisons.append((as_of_text, comparison))

        ledger_v1_hash = str(getattr(row, "v1_decision_hash"))
        ledger_v2_hash = str(getattr(row, "v2_decision_hash"))
        ledger_input_hash = str(getattr(row, "input_bundle_sha256"))
        ledger_commit = str(getattr(row, "code_commit"))

        artifact_consistent = all(
            [
                str(summary.get("as_of")) == as_of_text,
                bool(summary.get("shadow_week_valid")),
                str(summary.get("v1_decision_hash")) == ledger_v1_hash,
                str(summary.get("v2_decision_hash")) == ledger_v2_hash,
                str(summary.get("input_bundle_sha256")) == ledger_input_hash,
                str(summary.get("code_commit")) == ledger_commit,
                str(decision.get("decision_hash")) == ledger_v2_hash,
                str(decision.get("v1_decision_hash")) == ledger_v1_hash,
                str(decision.get("input_bundle_sha256")) == ledger_input_hash,
                str(
                    (fingerprints.get("files") or {}).get("sha256")
                ) == ledger_input_hash,
                str(
                    (fingerprints.get("code") or {}).get("commit")
                ) == ledger_commit,
            ]
        )

        coverage = weekly_coverage(current)
        weekly_rows.append(
            {
                "as_of": as_of_text,
                "artifact_consistent": artifact_consistent,
                "top10_overlap": int(getattr(row, "top10_overlap")),
                "entered_v2": str(getattr(row, "entered_v2") or ""),
                "exited_v2": str(getattr(row, "exited_v2") or ""),
                "pit_violations": int(getattr(row, "pit_violations")),
                **coverage,
                "v1_decision_hash": ledger_v1_hash,
                "v2_decision_hash": ledger_v2_hash,
                "input_bundle_sha256": ledger_input_hash,
                "code_commit": ledger_commit,
            }
        )

    weekly_detail = pd.DataFrame(weekly_rows).sort_values(
        "as_of", kind="stable"
    ).reset_index(drop=True)
    ticker_events = build_ticker_event_detail(comparisons)
    period_summary = build_period_summary(
        ledger,
        weekly_detail,
        ticker_events,
    )

    output_dir = ledger_dir / "evaluation_v1"
    output_dir.mkdir(parents=True, exist_ok=True)
    weekly_path = output_dir / "weekly_evidence.csv"
    ticker_path = output_dir / "ticker_events.csv"
    summary_path = output_dir / "summary.json"
    markdown_path = output_dir / "evaluation.md"

    weekly_detail.to_csv(weekly_path, index=False)
    ticker_events.to_csv(ticker_path, index=False)
    summary_path.write_text(
        json.dumps(period_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_evaluation_markdown(period_summary, weekly_detail),
        encoding="utf-8",
    )

    print("V2 TTM SHADOW EVALUATION PACKAGE")
    print(f"Protocol:                 {period_summary['evaluation_protocol']}")
    print(f"Valid shadow weeks:       {period_summary['valid_shadow_weeks']}")
    print(
        "Artifact consistency:    "
        f"{len(weekly_detail) - period_summary['weekly_artifact_consistency_failures']}"
        f"/{len(weekly_detail)}"
    )
    print(
        "Top-10 overlap mean/med: "
        f"{period_summary['top10_overlap_mean']:.2f}/"
        f"{period_summary['top10_overlap_median']:.2f}"
    )
    print(f"Weekly evidence:          {weekly_path}")
    print(f"Ticker events:            {ticker_path}")
    print(f"Summary:                  {summary_path}")
    print(f"Evaluation template:      {markdown_path}")
    print()
    print("NO MODEL, BROKER, ORDER, OR LIVE-AUTHORIZATION CHANGES WERE MADE.")


if __name__ == "__main__":
    main()
