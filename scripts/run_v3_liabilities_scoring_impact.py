from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths
from finance.research.v2_impact import score_long_growth_panel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V3-only scoring impact experiment for the 21 historically clean "
            "liabilities recoveries. Reads frozen V2 scoring inputs, applies "
            "recoveries only to an in-memory copy, and writes isolated V3 outputs."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _rank_current(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    current = frame.copy()
    current["decision_date"] = pd.to_datetime(
        current["decision_date"], errors="coerce"
    ).dt.normalize()
    current = current.loc[
        current["decision_date"].eq(as_of.normalize())
    ].copy()
    if current["ticker"].duplicated().any():
        raise ValueError("Duplicate current-date tickers in scored panel")

    current["selection_rank"] = pd.NA
    eligible = current.loc[
        current["top_conviction_eligible"].fillna(False).astype(bool)
        & pd.to_numeric(
            current["long_growth_v1_score"], errors="coerce"
        ).notna()
    ].sort_values(
        ["long_growth_v1_score", "ticker"],
        ascending=[False, True],
        kind="mergesort",
    )
    current.loc[eligible.index, "selection_rank"] = range(1, len(eligible) + 1)
    current["top10"] = False
    current.loc[eligible.head(10).index, "top10"] = True
    return current


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = pd.Timestamp(args.as_of)

    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    impact_dir = base / "v3_liabilities_impact"
    recovery_path = impact_dir / "recovery_detail.csv"
    if not recovery_path.exists():
        raise SystemExit(f"Missing V3 liabilities recovery detail: {recovery_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    scoring_panel_path = v2["scoring_panel"]
    if not scoring_panel_path.exists():
        raise SystemExit(f"Missing frozen V2 scoring panel: {scoring_panel_path}")

    recoveries = pd.read_csv(recovery_path, low_memory=False)
    recoveries["ticker"] = recoveries["ticker"].astype(str).str.upper().str.strip()
    recoveries["constructed_liabilities"] = pd.to_numeric(
        recoveries["constructed_liabilities"], errors="coerce"
    )
    recoveries = recoveries.loc[
        recoveries["constructed_liabilities"].gt(0)
    ].drop_duplicates("ticker", keep="first")
    recovery_map = recoveries.set_index("ticker")[
        "constructed_liabilities"
    ].to_dict()

    baseline_panel = pd.read_csv(scoring_panel_path, low_memory=False)
    baseline_panel["ticker"] = baseline_panel["ticker"].astype(str).str.upper().str.strip()
    baseline_panel["decision_date"] = pd.to_datetime(
        baseline_panel["decision_date"], errors="coerce"
    ).dt.normalize()

    current_mask = baseline_panel["decision_date"].eq(as_of.normalize())
    current_rows = baseline_panel.loc[current_mask].copy()
    missing_current = pd.to_numeric(
        current_rows["total_liabilities"], errors="coerce"
    ).le(0) | pd.to_numeric(
        current_rows["total_liabilities"], errors="coerce"
    ).isna()

    eligible_tickers = set(
        current_rows.loc[missing_current, "ticker"]
    ).intersection(recovery_map)
    if eligible_tickers != set(recovery_map):
        missing = sorted(set(recovery_map) - eligible_tickers)
        raise SystemExit(
            "Recovery set does not match missing-liabilities current rows: "
            + ", ".join(missing)
        )

    challenger_panel = baseline_panel.copy()
    apply_mask = (
        challenger_panel["decision_date"].eq(as_of.normalize())
        & challenger_panel["ticker"].isin(eligible_tickers)
    )
    challenger_panel.loc[apply_mask, "total_liabilities"] = (
        challenger_panel.loc[apply_mask, "ticker"].map(recovery_map)
    )

    baseline_scored = score_long_growth_panel(baseline_panel)
    challenger_scored = score_long_growth_panel(challenger_panel)

    baseline_current = _rank_current(baseline_scored, as_of)
    challenger_current = _rank_current(challenger_scored, as_of)

    metrics = [
        "ticker",
        "cik",
        "company_name",
        "total_liabilities",
        "liabilities_to_assets",
        "operating_cash_flow_to_liabilities",
        "financial_health_factor_count",
        "financial_health_eligible",
        "financial_health_score",
        "long_growth_v1_score",
        "top_conviction_eligible",
        "selection_rank",
        "top10",
    ]
    base_cols = [c for c in metrics if c in baseline_current.columns]
    chal_cols = [c for c in metrics if c in challenger_current.columns]

    before = baseline_current[base_cols].copy().rename(
        columns={c: f"baseline_{c}" for c in base_cols if c != "ticker"}
    )
    after = challenger_current[chal_cols].copy().rename(
        columns={c: f"v3_{c}" for c in chal_cols if c != "ticker"}
    )
    detail = before.merge(
        after, on="ticker", how="outer", validate="one_to_one"
    )

    detail["liabilities_recovered"] = detail["ticker"].isin(eligible_tickers)

    def numeric_change(name: str) -> None:
        left = f"baseline_{name}"
        right = f"v3_{name}"
        if left in detail.columns and right in detail.columns:
            detail[f"{name}_change"] = (
                pd.to_numeric(detail[right], errors="coerce")
                - pd.to_numeric(detail[left], errors="coerce")
            )

    numeric_change("financial_health_factor_count")
    numeric_change("financial_health_score")
    numeric_change("long_growth_v1_score")

    if {
        "baseline_top_conviction_eligible",
        "v3_top_conviction_eligible",
    }.issubset(detail.columns):
        base_top = detail["baseline_top_conviction_eligible"].fillna(False).astype(bool)
        v3_top = detail["v3_top_conviction_eligible"].fillna(False).astype(bool)
        detail["top_conviction_gained"] = ~base_top & v3_top
        detail["top_conviction_lost"] = base_top & ~v3_top
    else:
        base_top = pd.Series(False, index=detail.index)
        v3_top = pd.Series(False, index=detail.index)
        detail["top_conviction_gained"] = False
        detail["top_conviction_lost"] = False

    if {"baseline_top10", "v3_top10"}.issubset(detail.columns):
        base_top10 = detail["baseline_top10"].fillna(False).astype(bool)
        v3_top10 = detail["v3_top10"].fillna(False).astype(bool)
    else:
        base_top10 = pd.Series(False, index=detail.index)
        v3_top10 = pd.Series(False, index=detail.index)

    recovered_detail = detail.loc[
        detail["liabilities_recovered"]
    ].sort_values("ticker", kind="stable").reset_index(drop=True)

    summary = {
        "as_of": args.as_of.isoformat(),
        "universe_rows": int(len(detail)),
        "v3_liabilities_recoveries_applied": int(len(eligible_tickers)),
        "baseline_health_eligible": int(
            baseline_current["financial_health_eligible"]
            .fillna(False).astype(bool).sum()
        ),
        "v3_health_eligible": int(
            challenger_current["financial_health_eligible"]
            .fillna(False).astype(bool).sum()
        ),
        "health_eligibility_gain": int(
            challenger_current["financial_health_eligible"]
            .fillna(False).astype(bool).sum()
            - baseline_current["financial_health_eligible"]
            .fillna(False).astype(bool).sum()
        ),
        "recovered_names_health_gained": int(
            (
                recovered_detail.get(
                    "baseline_financial_health_eligible",
                    pd.Series(False, index=recovered_detail.index),
                ).fillna(False).astype(bool).eq(False)
                & recovered_detail.get(
                    "v3_financial_health_eligible",
                    pd.Series(False, index=recovered_detail.index),
                ).fillna(False).astype(bool)
            ).sum()
        ),
        "baseline_top_conviction_eligible": int(base_top.sum()),
        "v3_top_conviction_eligible": int(v3_top.sum()),
        "top_conviction_gained": int((~base_top & v3_top).sum()),
        "top_conviction_lost": int((base_top & ~v3_top).sum()),
        "baseline_top10_count": int(base_top10.sum()),
        "v3_top10_count": int(v3_top10.sum()),
        "top10_overlap": int((base_top10 & v3_top10).sum()),
        "top10_entered": int((~base_top10 & v3_top10).sum()),
        "top10_exited": int((base_top10 & ~v3_top10).sum()),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "broker_capability": False,
        "order_capability": False,
    }

    output_dir = impact_dir / "scoring_experiment"
    output_dir.mkdir(parents=True, exist_ok=True)
    recovered_path = output_dir / "recovered_ticker_impact.csv"
    all_path = output_dir / "universe_impact.csv"
    summary_path = output_dir / "summary.json"

    recovered_detail.to_csv(recovered_path, index=False)
    detail.to_csv(all_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3-ONLY LIABILITIES SCORING IMPACT EXPERIMENT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Recoveries applied:          {summary['v3_liabilities_recoveries_applied']}")
    print(
        f"Health eligible:             "
        f"{summary['baseline_health_eligible']} -> "
        f"{summary['v3_health_eligible']} "
        f"({summary['health_eligibility_gain']:+d})"
    )
    print(
        f"Recovered names gaining Health eligibility: "
        f"{summary['recovered_names_health_gained']}"
    )
    print(
        f"Top-conviction eligible:     "
        f"{summary['baseline_top_conviction_eligible']} -> "
        f"{summary['v3_top_conviction_eligible']} "
        f"(+{summary['top_conviction_gained']}/"
        f"-{summary['top_conviction_lost']})"
    )
    print(f"Top-10 overlap:              {summary['top10_overlap']}/10")
    print(
        f"Top-10 entered/exited:       "
        f"{summary['top10_entered']}/{summary['top10_exited']}"
    )
    print(f"Recovered detail:            {recovered_path}")
    print(f"Universe impact:             {all_path}")
    print(f"Summary:                     {summary_path}")
    print("V1 AND FROZEN V2 FILES WERE READ-ONLY AND NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
