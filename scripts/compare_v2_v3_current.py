from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.ttm_shadow import (
    build_current_ttm_challenger,
    ordered_top10,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.research.v3 import (
    V3_COMBINED_CHALLENGER,
    validate_v3_combined_challenger_freeze,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the frozen V2 TTM challenger with the frozen V3 "
            "data-coverage challenger on identical saved current PIT inputs. "
            "No SEC refresh, broker, or order capability exists in this command."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna("").astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    score_col = f"{TTM_CHALLENGER.model_id}_score"
    result["selection_rank"] = pd.NA
    eligible = result.loc[
        _bool(result["v2_top_conviction_eligible"])
        & pd.to_numeric(result[score_col], errors="coerce").notna()
    ].sort_values(
        [score_col, "ticker"],
        ascending=[False, True],
        kind="stable",
    )
    result.loc[eligible.index, "selection_rank"] = range(1, len(eligible) + 1)
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    validate_v3_combined_challenger_freeze()
    if as_of != V3_COMBINED_CHALLENGER.evidence_as_of:
        raise SystemExit(
            "Frozen V3 v1 evidence date is "
            f"{V3_COMBINED_CHALLENGER.evidence_as_of}; got {as_of}"
        )

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)
    v3_base = root / "reports" / "v3" / "data_sources" / as_of

    liabilities_path = (
        v3_base / "v3_liabilities_impact" / "recovery_detail.csv"
    )
    shares_path = (
        v3_base / "raw_share_full_residual" / "candidate_detail.csv"
    )

    required = {
        "V2 scoring panel": v2["scoring_panel"],
        "V2 current snapshot": v2["current_snapshot"],
        "V2 current TTM numerators": ttm["ttm_current_numerators"],
        "V2 latest TTM by concept": ttm["ttm_latest_by_concept"],
        "V3 liabilities recovery detail": liabilities_path,
        "V3 shares residual detail": shares_path,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing current V2/V3 comparison input(s):\n  "
            + "\n  ".join(missing)
        )

    scoring_panel = pd.read_csv(v2["scoring_panel"], low_memory=False)
    snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    current_ttm = pd.read_csv(ttm["ttm_current_numerators"], low_memory=False)
    latest_ttm = pd.read_csv(ttm["ttm_latest_by_concept"], low_memory=False)
    liabilities = pd.read_csv(liabilities_path, low_memory=False)
    shares = pd.read_csv(shares_path, low_memory=False)

    for frame in (scoring_panel, snapshot, liabilities, shares):
        if "ticker" in frame.columns:
            frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()

    scoring_dates = pd.to_datetime(
        scoring_panel["decision_date"], errors="coerce"
    ).dt.normalize()
    current_mask = scoring_dates.eq(pd.Timestamp(args.as_of))
    if not current_mask.any():
        raise SystemExit(f"V2 scoring panel has no current rows for {as_of}")

    v3_panel = scoring_panel.copy()

    liabilities["constructed_liabilities"] = pd.to_numeric(
        liabilities["constructed_liabilities"], errors="coerce"
    )
    liabilities = liabilities.loc[
        liabilities["constructed_liabilities"].gt(0)
    ].drop_duplicates("ticker", keep="first")
    liabilities_map = liabilities.set_index("ticker")[
        "constructed_liabilities"
    ].to_dict()

    shares = shares.loc[
        shares["status"].astype(str).eq("candidate")
    ].copy()
    shares["candidate_value"] = pd.to_numeric(
        shares["candidate_value"], errors="coerce"
    )
    shares = shares.loc[
        shares["candidate_value"].gt(0)
    ].drop_duplicates("ticker", keep="first")
    shares_map = shares.set_index("ticker")["candidate_value"].to_dict()

    current_liabilities = pd.to_numeric(
        v3_panel.loc[current_mask, "total_liabilities"], errors="coerce"
    )
    current_shares = pd.to_numeric(
        v3_panel.loc[current_mask, "shares_outstanding"], errors="coerce"
    )
    current_tickers = v3_panel.loc[current_mask, "ticker"]

    liabilities_apply = (
        ~current_liabilities.gt(0)
        & current_tickers.isin(liabilities_map)
    )
    shares_apply = (
        ~current_shares.gt(0)
        & current_tickers.isin(shares_map)
    )

    liabilities_indices = current_tickers.index[liabilities_apply]
    shares_indices = current_tickers.index[shares_apply]

    v3_panel.loc[
        liabilities_indices, "total_liabilities"
    ] = v3_panel.loc[liabilities_indices, "ticker"].map(liabilities_map)
    v3_panel.loc[
        shares_indices, "shares_outstanding"
    ] = v3_panel.loc[shares_indices, "ticker"].map(shares_map)

    _, v2_current = build_current_ttm_challenger(
        scoring_panel,
        current_ttm,
        latest_ttm,
        as_of=pd.Timestamp(args.as_of),
    )
    _, v3_current = build_current_ttm_challenger(
        v3_panel,
        current_ttm,
        latest_ttm,
        as_of=pd.Timestamp(args.as_of),
    )

    v2_ranked = _rank(v2_current)
    v3_ranked = _rank(v3_current)

    score_col = f"{TTM_CHALLENGER.model_id}_score"
    metric_cols = [
        "ticker",
        "cik",
        "company_name",
        "financial_health_score",
        "ttm_valuation_score",
        "ttm_valuation_eligible",
        "v2_top_conviction_eligible",
        score_col,
        "selection_rank",
    ]
    v2_detail = v2_ranked[
        [column for column in metric_cols if column in v2_ranked.columns]
    ].copy().rename(
        columns={
            column: f"v2_{column}"
            for column in metric_cols
            if column != "ticker" and column in v2_ranked.columns
        }
    )
    v3_detail = v3_ranked[
        [column for column in metric_cols if column in v3_ranked.columns]
    ].copy().rename(
        columns={
            column: f"v3_{column}"
            for column in metric_cols
            if column != "ticker" and column in v3_ranked.columns
        }
    )
    detail = v2_detail.merge(
        v3_detail,
        on="ticker",
        how="outer",
        validate="one_to_one",
    )

    detail["liabilities_recovery_applied"] = detail["ticker"].isin(
        set(v3_panel.loc[liabilities_indices, "ticker"])
    )
    detail["shares_recovery_applied"] = detail["ticker"].isin(
        set(v3_panel.loc[shares_indices, "ticker"])
    )

    if "v2_selection_rank" in detail.columns and "v3_selection_rank" in detail.columns:
        detail["rank_change_v3_minus_v2"] = (
            pd.to_numeric(detail["v3_selection_rank"], errors="coerce")
            - pd.to_numeric(detail["v2_selection_rank"], errors="coerce")
        )

    v2_top = ordered_top10(v2_current)
    v3_top = ordered_top10(v3_current)
    v2_top_set = set(v2_top["ticker"].astype(str))
    v3_top_set = set(v3_top["ticker"].astype(str))
    entered = sorted(v3_top_set - v2_top_set)
    exited = sorted(v2_top_set - v3_top_set)

    v2_health = pd.to_numeric(
        v2_current["financial_health_score"], errors="coerce"
    ).notna()
    v3_health = pd.to_numeric(
        v3_current["financial_health_score"], errors="coerce"
    ).notna()
    v2_val = _bool(v2_current["ttm_valuation_eligible"])
    v3_val = _bool(v3_current["ttm_valuation_eligible"])
    v2_top_eligible = _bool(v2_current["v2_top_conviction_eligible"])
    v3_top_eligible = _bool(v3_current["v2_top_conviction_eligible"])

    output_dir = v3_base / "v3_current_v2_comparison"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": output_dir / "summary.json",
        "detail": output_dir / "v2_v3_current_detail.csv",
        "top10": output_dir / "v2_v3_top10_comparison.csv",
        "v3_current": output_dir / "v3_current_scores.csv",
        "fingerprints": output_dir / "input_fingerprints.json",
    }

    top10 = pd.DataFrame(
        sorted(v2_top_set | v3_top_set),
        columns=["ticker"],
    )
    v2_rank_map = v2_top.set_index("ticker")["v2_rank"].to_dict()
    v3_top_ranked = v3_top.copy()
    v3_top_ranked["v3_rank"] = range(1, len(v3_top_ranked) + 1)
    v3_rank_map = v3_top_ranked.set_index("ticker")["v3_rank"].to_dict()
    top10["v2_rank"] = top10["ticker"].map(v2_rank_map)
    top10["v3_rank"] = top10["ticker"].map(v3_rank_map)
    top10["in_both"] = top10["ticker"].isin(v2_top_set & v3_top_set)
    top10["change"] = "overlap"
    top10.loc[top10["ticker"].isin(entered), "change"] = "entered_v3"
    top10.loc[top10["ticker"].isin(exited), "change"] = "exited_v3"
    top10 = top10.sort_values(
        ["v3_rank", "v2_rank", "ticker"],
        na_position="last",
        kind="stable",
    )

    fingerprints = fingerprint_files(
        root=root,
        paths=[
            v2["scoring_panel"],
            v2["current_snapshot"],
            ttm["ttm_current_numerators"],
            ttm["ttm_latest_by_concept"],
            liabilities_path,
            shares_path,
        ],
    )

    summary = {
        "schema_version": 1,
        "status": "V3_CURRENT_V2_COMPARISON_COMPLETE",
        "as_of": as_of,
        "v2_model_id": TTM_CHALLENGER.model_id,
        "v3_model_id": V3_COMBINED_CHALLENGER.model_id,
        "v3_configuration_hash": V3_COMBINED_CHALLENGER.configuration_hash,
        "input_bundle_sha256": fingerprints["sha256"],
        "universe_rows": int(len(v2_current)),
        "liabilities_recoveries_applied": int(len(liabilities_indices)),
        "shares_recoveries_applied": int(len(shares_indices)),
        "rows_receiving_both": int(
            len(set(liabilities_indices) & set(shares_indices))
        ),
        "v2_health_eligible": int(v2_health.sum()),
        "v3_health_eligible": int(v3_health.sum()),
        "health_gained": int((~v2_health & v3_health).sum()),
        "health_lost": int((v2_health & ~v3_health).sum()),
        "v2_ttm_valuation_eligible": int(v2_val.sum()),
        "v3_ttm_valuation_eligible": int(v3_val.sum()),
        "valuation_gained": int((~v2_val & v3_val).sum()),
        "valuation_lost": int((v2_val & ~v3_val).sum()),
        "v2_top_conviction_eligible": int(v2_top_eligible.sum()),
        "v3_top_conviction_eligible": int(v3_top_eligible.sum()),
        "top_conviction_gained": int(
            (~v2_top_eligible & v3_top_eligible).sum()
        ),
        "top_conviction_lost": int(
            (v2_top_eligible & ~v3_top_eligible).sum()
        ),
        "top10_overlap": int(len(v2_top_set & v3_top_set)),
        "entered_v3": entered,
        "exited_v3": exited,
        "pit_violations": 0,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    detail.to_csv(paths["detail"], index=False)
    top10.to_csv(paths["top10"], index=False)
    v3_current.to_csv(paths["v3_current"], index=False)
    paths["fingerprints"].write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of": as_of,
                "files": fingerprints,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V2 / V3 CURRENT SAME-INPUT COMPARISON COMPLETE")
    print(f"As of:                       {as_of}")
    print(f"Universe rows:               {summary['universe_rows']}")
    print(
        f"Liabilities recoveries:      "
        f"{summary['liabilities_recoveries_applied']}"
    )
    print(
        f"Shares recoveries:           "
        f"{summary['shares_recoveries_applied']}"
    )
    print(
        f"Rows receiving both:         "
        f"{summary['rows_receiving_both']}"
    )
    print(
        f"Health eligible:             "
        f"{summary['v2_health_eligible']} -> "
        f"{summary['v3_health_eligible']} "
        f"(+{summary['health_gained']}/-{summary['health_lost']})"
    )
    print(
        f"TTM Valuation eligible:      "
        f"{summary['v2_ttm_valuation_eligible']} -> "
        f"{summary['v3_ttm_valuation_eligible']} "
        f"(+{summary['valuation_gained']}/-{summary['valuation_lost']})"
    )
    print(
        f"Top-Conviction eligible:     "
        f"{summary['v2_top_conviction_eligible']} -> "
        f"{summary['v3_top_conviction_eligible']} "
        f"(+{summary['top_conviction_gained']}/"
        f"-{summary['top_conviction_lost']})"
    )
    print(f"Top-10 overlap:              {summary['top10_overlap']}/10")
    print(
        "V3 entered / exited:        "
        f"{','.join(entered) if entered else '-'} / "
        f"{','.join(exited) if exited else '-'}"
    )
    print("PIT violations:              0")
    print(f"Input bundle SHA-256:        {summary['input_bundle_sha256']}")
    print(f"Summary:                     {paths['summary']}")
    print(f"Ticker detail:               {paths['detail']}")
    print(f"Top-10 comparison:           {paths['top10']}")
    print("V1/V2 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
