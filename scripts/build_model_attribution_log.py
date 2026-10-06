from __future__ import annotations

"""Build a read-only attribution log for challenger Top-10 differences."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _rank_map(decision: dict[str, Any]) -> dict[str, int]:
    rows = decision.get("top10")
    if rows is None:
        rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("Decision is missing ranked rows")
    result: dict[str, int] = {}
    for row in rows:
        rank = int(row["rank"])
        if rank <= 10:
            ticker = str(row["ticker"]).strip().upper()
            if ticker in result:
                raise ValueError(f"Duplicate ticker in Top-10: {ticker}")
            result[ticker] = rank
    if len(result) != 10:
        raise ValueError("Expected 10 unique Top-10 tickers")
    return result


def _current_score_rows(path: Path) -> dict[str, dict[str, object]]:
    frame = pd.read_csv(path, low_memory=False)
    if "ticker" not in frame.columns:
        raise ValueError(f"Missing ticker column: {path}")
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    if frame["ticker"].duplicated().any():
        raise ValueError(f"Duplicate ticker rows: {path}")
    return {
        str(row["ticker"]): row.to_dict()
        for _, row in frame.iterrows()
    }


def classify_v2_change(
    ticker: str,
    *,
    direction: str,
    score_row: dict[str, object] | None,
) -> tuple[str, str]:
    if score_row is None:
        return "unresolved_missing_score_row", "No saved V2 score row was available."

    annual_eligible = _bool(score_row.get("top_conviction_eligible"))
    ttm_eligible = _bool(score_row.get("v2_top_conviction_eligible"))

    if direction == "entered":
        if not annual_eligible and ttm_eligible:
            return (
                "ttm_eligibility_gain",
                "Ticker was not V1 Top-Conviction eligible but became V2 TTM Top-Conviction eligible.",
            )
        return (
            "ttm_score_rank_change",
            "Ticker remained eligible or eligibility was unchanged; entry is attributed to the V2 TTM score/rank ordering.",
        )

    if annual_eligible and not ttm_eligible:
        return (
            "ttm_eligibility_loss",
            "Ticker was V1 Top-Conviction eligible but is not V2 TTM Top-Conviction eligible.",
        )
    return (
        "ttm_score_rank_displacement",
        "Ticker remained eligible or eligibility was unchanged; exit is attributed to V2 TTM score/rank displacement.",
    )


def _recovery_map(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty or "ticker" not in frame.columns:
        return {}
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    result: dict[str, dict[str, object]] = {}
    for _, row in frame.iterrows():
        ticker = str(row["ticker"])
        shares = (
            str(row.get("shares_status") or "") == "candidate"
            and pd.to_numeric(row.get("candidate_shares"), errors="coerce") > 0
        )
        liabilities = (
            str(row.get("liabilities_status") or "")
            == "current_plus_noncurrent_candidate"
            and pd.to_numeric(row.get("constructed_liabilities"), errors="coerce") > 0
        )
        if shares or liabilities:
            result[ticker] = {
                "shares_recovered": bool(shares),
                "liabilities_recovered": bool(liabilities),
            }
    return result


def classify_v3_change(
    ticker: str,
    *,
    direction: str,
    recovery: dict[str, dict[str, object]],
    direct_changed_entrants: set[str],
) -> tuple[str, str]:
    row = recovery.get(ticker)
    if direction == "entered" and row:
        fields = []
        if row["shares_recovered"]:
            fields.append("shares")
        if row["liabilities_recovered"]:
            fields.append("liabilities")
        return (
            "direct_v3_data_recovery",
            "Ticker entered after direct recovery of " + " and ".join(fields) + ".",
        )

    if direction == "exited" and direct_changed_entrants:
        return (
            "indirect_displacement_from_v3_recovery",
            "Ticker was displaced while a directly recovered ticker entered the V3 Top-10.",
        )

    if row:
        return (
            "v3_rank_change_on_recovered_ticker",
            "Ticker itself has recovered V3 data, but the saved artifacts do not prove the recovery alone caused this membership change.",
        )

    return (
        "indirect_v3_rerank",
        "Membership changed after the V3 data-recovery rerank, but no direct recovery exists on this ticker.",
    )


def build_attribution(root: Path) -> tuple[list[dict[str, object]], dict[str, Any]]:
    rows: list[dict[str, object]] = []
    v2_root = root / "reports" / "v2" / "long_growth_v2_research"
    if not v2_root.exists():
        return rows, {"schema_version": 1, "events": 0, "reason_counts": {}}

    for child in sorted(v2_root.iterdir()):
        if not child.is_dir():
            continue
        as_of = child.name
        v1_path = root / "reports" / "shadow" / as_of / "shadow_decision.json"
        v2_decision_path = child / "ttm_shadow" / "v2_shadow_decision.json"
        v2_scores_path = child / "ttm_shadow" / "v2_current_scores.csv"
        v3_dir = (
            root / "reports" / "v3" / "long_growth_v3_data_coverage_v1"
            / as_of / "shadow"
        )
        v3_decision_path = v3_dir / "v3_shadow_decision.json"

        if not (v1_path.exists() and v2_decision_path.exists() and v2_scores_path.exists()):
            continue

        v1 = _rank_map(read_json(v1_path))
        v2 = _rank_map(read_json(v2_decision_path))
        score_rows = _current_score_rows(v2_scores_path)

        for ticker in sorted(set(v1) ^ set(v2)):
            direction = "entered" if ticker in v2 else "exited"
            reason, detail = classify_v2_change(
                ticker,
                direction=direction,
                score_row=score_rows.get(ticker),
            )
            score = score_rows.get(ticker, {})
            rows.append({
                "as_of": as_of,
                "transition": "v1_to_v2",
                "ticker": ticker,
                "direction": direction,
                "from_rank": v1.get(ticker),
                "to_rank": v2.get(ticker),
                "reason": reason,
                "reason_detail": detail,
                "annual_top_conviction_eligible": _bool(score.get("top_conviction_eligible")),
                "ttm_top_conviction_eligible": _bool(score.get("v2_top_conviction_eligible")),
                "shares_recovered": False,
                "liabilities_recovered": False,
            })

        if not v3_decision_path.exists():
            continue
        v3 = _rank_map(read_json(v3_decision_path))
        recovery = _recovery_map(v3_dir / "recovery_detail.csv")
        changed_entrants = set(v3) - set(v2)
        direct_changed_entrants = {
            ticker for ticker in changed_entrants if ticker in recovery
        }

        for ticker in sorted(set(v2) ^ set(v3)):
            direction = "entered" if ticker in v3 else "exited"
            reason, detail = classify_v3_change(
                ticker,
                direction=direction,
                recovery=recovery,
                direct_changed_entrants=direct_changed_entrants,
            )
            rec = recovery.get(ticker, {})
            rows.append({
                "as_of": as_of,
                "transition": "v2_to_v3",
                "ticker": ticker,
                "direction": direction,
                "from_rank": v2.get(ticker),
                "to_rank": v3.get(ticker),
                "reason": reason,
                "reason_detail": detail,
                "annual_top_conviction_eligible": None,
                "ttm_top_conviction_eligible": None,
                "shares_recovered": bool(rec.get("shares_recovered", False)),
                "liabilities_recovered": bool(rec.get("liabilities_recovered", False)),
            })

    reason_counts: dict[str, int] = {}
    transition_counts: dict[str, int] = {}
    for row in rows:
        reason = str(row["reason"])
        transition = str(row["transition"])
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        transition_counts[transition] = transition_counts.get(transition, 0) + 1

    summary = {
        "schema_version": 1,
        "evaluation_only": True,
        "events": len(rows),
        "reason_counts": dict(sorted(reason_counts.items())),
        "transition_counts": dict(sorted(transition_counts.items())),
        "causality_note": (
            "Attribution uses saved eligibility/recovery evidence. Indirect/rerank "
            "labels intentionally avoid claiming causality unsupported by artifacts."
        ),
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build read-only challenger attribution log")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/model_comparison/attribution"),
    )
    args = parser.parse_args()
    root = args.repo_root.resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    rows, summary = build_attribution(root)
    fields = [
        "as_of", "transition", "ticker", "direction", "from_rank", "to_rank",
        "reason", "reason_detail", "annual_top_conviction_eligible",
        "ttm_top_conviction_eligible", "shares_recovered", "liabilities_recovered",
    ]
    with (output / "selection_attribution_log.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("CHALLENGER SELECTION ATTRIBUTION LOG")
    print("Evaluation only:          YES")
    print(f"Attribution events:       {summary['events']}")
    for reason, count in summary["reason_counts"].items():
        print(f"{reason}: {count}")
    print(f"Output:                   {output}")


if __name__ == "__main__":
    main()
