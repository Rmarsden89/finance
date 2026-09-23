from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finance.factors.validation import validate_raw_factors
from finance.factors.valuation import add_valuation_factors
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_historical_replay import (
    audit_historical_ttm_pit,
    build_quarter_events,
    build_ttm_events,
    replay_ttm_numerators_to_panel,
)
from finance.research.ttm_valuation_family import (
    ANNUAL_VALUATION_WEIGHTS,
    add_ttm_valuation_factors,
    add_ttm_valuation_family_score,
    normalize_ttm_valuation_factors,
    score_weighted_family,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.scoring.normalize import normalize_validated_factors


FACTOR_PAIRS = {
    "earnings_yield": ("earnings_yield_annual", "earnings_yield_ttm"),
    "sales_yield": ("sales_yield_annual", "sales_yield_ttm"),
    "free_cash_flow_yield": (
        "free_cash_flow_yield_annual",
        "free_cash_flow_yield_ttm",
    ),
    "book_to_market": ("book_to_market", "book_to_market"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the YTD-preferred TTM valuation family through the "
            "historical V1 research panel using PIT SEC event states."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--rebuild-events",
        action="store_true",
        help="Rebuild the reusable quarter/TTM event cache.",
    )
    return parser.parse_args()


def _resolve_manifest_path(
    root: Path,
    value: object,
    label: str,
) -> Path:
    if not value:
        raise SystemExit(f"Completed V2 manifest lacks {label}")
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise SystemExit(f"Missing {label}: {path}")
    return path


def _event_cache(
    *,
    root: Path,
    duration_path: Path,
    paths: dict[str, Path],
    rebuild: bool,
) -> tuple[pd.DataFrame, dict[str, object]]:
    summary_path = paths["ttm_history_event_summary"]
    quarter_path = paths["ttm_history_quarter_events"]
    ttm_path = paths["ttm_history_ttm_events"]

    reusable = (
        not rebuild
        and summary_path.exists()
        and quarter_path.exists()
        and ttm_path.exists()
    )
    if reusable:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("status") != "TTM_HISTORICAL_EVENT_CACHE_COMPLETE":
            raise SystemExit(
                "Existing TTM historical event cache is not complete"
            )
        print("Reusing completed historical TTM event cache...", flush=True)
        return pd.read_csv(ttm_path, low_memory=False), summary

    if rebuild and paths["ttm_history_event_cache_dir"].exists():
        raise SystemExit(
            "--rebuild-events requires the existing event-cache directory "
            "to be preserved/renamed or removed first: "
            f"{paths['ttm_history_event_cache_dir']}"
        )
    if paths["ttm_history_event_cache_dir"].exists():
        raise SystemExit(
            "Partial historical TTM event cache exists. Preserve/rename or "
            f"remove it before rebuilding: {paths['ttm_history_event_cache_dir']}"
        )

    print("Loading V2 duration winners for historical event build...", flush=True)
    duration = pd.read_csv(duration_path, low_memory=False)
    print(f"Duration winner rows:       {len(duration):,}", flush=True)

    print("Building versioned discrete-quarter events...", flush=True)
    quarter_events = build_quarter_events(
        duration,
        progress=lambda message: print(message, flush=True),
    )
    print(
        f"Quarter events complete:    {len(quarter_events):,}",
        flush=True,
    )

    print("Building versioned four-quarter TTM events...", flush=True)
    ttm_events = build_ttm_events(
        quarter_events,
        progress=lambda message: print(message, flush=True),
    )
    print(f"TTM events complete:        {len(ttm_events):,}", flush=True)

    cache_dir = paths["ttm_history_event_cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=False)
    quarter_events.to_csv(quarter_path, index=False)
    ttm_events.to_csv(ttm_path, index=False)

    summary = {
        "schema_version": 1,
        "status": "TTM_HISTORICAL_EVENT_CACHE_COMPLETE",
        "policy": "ytd_preferred",
        "duration_winner_rows": len(duration),
        "quarter_event_rows": len(quarter_events),
        "ttm_event_rows": len(ttm_events),
        "concepts": sorted(
            str(value) for value in ttm_events["concept"].dropna().unique()
        ) if not ttm_events.empty else [],
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["ttm_history_event_fingerprints"].write_text(
        json.dumps(
            {
                "schema_version": 1,
                "direct_inputs": fingerprint_files(
                    root=root, paths=[duration_path]
                ),
                "code": git_provenance(root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ttm_events, summary


def _latest_rows_for_date(replay: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for concept, prefix in (
        ("revenue", "revenue"),
        ("net_income", "net_income"),
    ):
        subset = replay[
            [
                "cik",
                f"ttm_{prefix}_available_at",
                f"ttm_{prefix}_end_date",
            ]
        ].copy()
        subset = (
            subset.loc[subset["cik"].notna()]
            .drop_duplicates("cik", keep="last")
            .copy()
        )
        subset["concept"] = concept
        subset = subset.rename(
            columns={
                f"ttm_{prefix}_available_at": "available_at",
                f"ttm_{prefix}_end_date": "ttm_end_date",
            }
        )
        rows.append(subset)
    return pd.concat(rows, ignore_index=True)


def _score_historical_ttm(
    panel: pd.DataFrame,
    replay: pd.DataFrame,
    annual: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    dates = sorted(pd.to_datetime(panel["decision_date"]).dt.date.unique())
    for position, decision_day in enumerate(dates, start=1):
        date_text = str(decision_day)
        snapshot = panel.loc[
            pd.to_datetime(panel["decision_date"]).dt.date.eq(decision_day)
        ].copy()
        current_ttm = replay.loc[
            pd.to_datetime(replay["decision_date"]).dt.date.eq(decision_day)
        ].copy()
        latest = _latest_rows_for_date(current_ttm)
        scored = add_ttm_valuation_factors(snapshot, current_ttm, latest)
        frames.append(scored)

        if position == 1 or position % 50 == 0 or position == len(dates):
            print(
                f"TTM factor build {position}/{len(dates)} "
                f"date={date_text} rows={len(scored):,}",
                flush=True,
            )

    result = pd.concat(frames, ignore_index=True, sort=False)

    book_cols = [
        "decision_date",
        "ticker",
        "book_to_market",
        "book_to_market_valid",
        "book_to_market_invalid_reason",
        "book_to_market_validated",
        "book_to_market_winsorized",
        "book_to_market_winsorized_flag",
        "book_to_market_percentile",
        "book_to_market_score",
    ]
    available_book = [c for c in book_cols if c in annual.columns]
    result = result.drop(
        columns=[c for c in book_cols[2:] if c in result.columns],
        errors="ignore",
    ).merge(
        annual[available_book],
        on=["decision_date", "ticker"],
        how="left",
        validate="one_to_one",
    )
    result = normalize_ttm_valuation_factors(result)
    return add_ttm_valuation_family_score(result)


def _coverage_by_year(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> pd.DataFrame:
    a = annual.copy()
    t = ttm.copy()
    a["year"] = pd.to_datetime(a["decision_date"]).dt.year
    t["year"] = pd.to_datetime(t["decision_date"]).dt.year
    rows: list[dict[str, object]] = []

    years = sorted(set(a["year"].dropna().astype(int)))
    for year in [None, *years]:
        aa = a if year is None else a.loc[a["year"].eq(year)]
        tt = t if year is None else t.loc[t["year"].eq(year)]
        rows.append(
            {
                "year": "ALL" if year is None else int(year),
                "rows": len(aa),
                "decision_dates": int(aa["decision_date"].nunique()),
                "annual_valuation_eligible": int(
                    aa["annual_valuation_eligible"].fillna(False).sum()
                ),
                "ttm_valuation_eligible": int(
                    tt["ttm_valuation_eligible"].fillna(False).sum()
                ),
                "annual_earnings_available": int(
                    aa["earnings_yield_annual_validated"].notna().sum()
                ),
                "ttm_earnings_available": int(
                    tt["earnings_yield_ttm_validated"].notna().sum()
                ),
                "annual_sales_available": int(
                    aa["sales_yield_annual_validated"].notna().sum()
                ),
                "ttm_sales_available": int(
                    tt["sales_yield_ttm_validated"].notna().sum()
                ),
                "annual_fcf_available": int(
                    aa["free_cash_flow_yield_annual_validated"].notna().sum()
                ),
                "ttm_fcf_available": int(
                    tt["free_cash_flow_yield_ttm_validated"].notna().sum()
                ),
                "book_to_market_available": int(
                    aa["book_to_market_validated"].notna().sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def _correlations_by_year(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> pd.DataFrame:
    keys = ["decision_date", "ticker"]
    merged = annual.merge(
        ttm,
        on=keys,
        how="inner",
        suffixes=("_annual_panel", "_ttm_panel"),
        validate="one_to_one",
    )
    merged["year"] = pd.to_datetime(merged["decision_date"]).dt.year
    rows: list[dict[str, object]] = []

    for year, group in [("ALL", merged), *list(merged.groupby("year"))]:
        for label, (annual_factor, ttm_factor) in FACTOR_PAIRS.items():
            if label == "book_to_market":
                left_col = "book_to_market_validated_annual_panel"
                right_col = "book_to_market_validated_ttm_panel"
            else:
                left_col = f"{annual_factor}_validated"
                right_col = f"{ttm_factor}_validated"
                if left_col not in group.columns:
                    left_col = f"{left_col}_annual_panel"
                if right_col not in group.columns:
                    right_col = f"{right_col}_ttm_panel"

            pair = group[[left_col, right_col]].apply(
                pd.to_numeric, errors="coerce"
            ).dropna()
            rows.append(
                {
                    "year": year,
                    "factor_pair": label,
                    "overlap_rows": len(pair),
                    "pearson": (
                        pair.corr(method="pearson").iloc[0, 1]
                        if len(pair) >= 2 else np.nan
                    ),
                    "spearman": (
                        pair.corr(method="spearman").iloc[0, 1]
                        if len(pair) >= 2 else np.nan
                    ),
                }
            )

        score_pair = group[
            ["annual_valuation_score", "ttm_valuation_score"]
        ].apply(pd.to_numeric, errors="coerce").dropna()
        rows.append(
            {
                "year": year,
                "factor_pair": "valuation_family_score",
                "overlap_rows": len(score_pair),
                "pearson": (
                    score_pair.corr(method="pearson").iloc[0, 1]
                    if len(score_pair) >= 2 else np.nan
                ),
                "spearman": (
                    score_pair.corr(method="spearman").iloc[0, 1]
                    if len(score_pair) >= 2 else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def _rank_and_weekly_summary(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    a = annual[
        [
            "decision_date", "ticker", "cik",
            "annual_valuation_eligible", "annual_valuation_score",
        ]
    ].copy()
    t = ttm[
        [
            "decision_date", "ticker", "cik",
            "ttm_valuation_eligible", "ttm_valuation_score",
        ]
    ].copy()
    merged = a.merge(
        t,
        on=["decision_date", "ticker", "cik"],
        how="outer",
        validate="one_to_one",
    )

    rank_frames: list[pd.DataFrame] = []
    weekly_rows: list[dict[str, object]] = []
    for decision_date, group in merged.groupby("decision_date", sort=True):
        group = group.copy()
        annual_score = pd.to_numeric(
            group["annual_valuation_score"], errors="coerce"
        )
        ttm_score = pd.to_numeric(
            group["ttm_valuation_score"], errors="coerce"
        )
        annual_ok = (
            group["annual_valuation_eligible"].fillna(False).astype(bool)
            & annual_score.notna()
        )
        ttm_ok = (
            group["ttm_valuation_eligible"].fillna(False).astype(bool)
            & ttm_score.notna()
        )
        group["annual_rank"] = annual_score.where(annual_ok).rank(
            method="min", ascending=False, na_option="keep"
        )
        group["ttm_rank"] = ttm_score.where(ttm_ok).rank(
            method="min", ascending=False, na_option="keep"
        )
        overlap = group["annual_rank"].notna() & group["ttm_rank"].notna()
        group["rank_change_ttm_minus_annual"] = (
            group["ttm_rank"] - group["annual_rank"]
        ).where(overlap)
        group["absolute_rank_change"] = (
            group["rank_change_ttm_minus_annual"].abs()
        )

        annual_top = set(
            group.loc[group["annual_rank"].le(10), "ticker"].astype(str)
        )
        ttm_top = set(
            group.loc[group["ttm_rank"].le(10), "ticker"].astype(str)
        )
        score_pair = group.loc[
            overlap, ["annual_valuation_score", "ttm_valuation_score"]
        ].apply(pd.to_numeric, errors="coerce").dropna()
        weekly_rows.append(
            {
                "decision_date": decision_date,
                "rows": len(group),
                "annual_eligible": int(annual_ok.sum()),
                "ttm_eligible": int(ttm_ok.sum()),
                "overlap_scored": int(overlap.sum()),
                "family_score_spearman": (
                    score_pair.corr(method="spearman").iloc[0, 1]
                    if len(score_pair) >= 2 else np.nan
                ),
                "median_absolute_rank_change": (
                    float(group.loc[overlap, "absolute_rank_change"].median())
                    if overlap.any() else np.nan
                ),
                "top10_overlap": len(annual_top & ttm_top),
                "annual_top10_count": len(annual_top),
                "ttm_top10_count": len(ttm_top),
            }
        )
        rank_frames.append(group)

    return (
        pd.concat(rank_frames, ignore_index=True),
        pd.DataFrame(weekly_rows),
    )


def _turnover(rank: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for variant in ("annual", "ttm"):
        rank_col = f"{variant}_rank"
        prior: set[str] | None = None
        transitions = 0
        replacements: list[int] = []
        for _, group in rank.groupby("decision_date", sort=True):
            current = set(
                group.loc[group[rank_col].le(10), "ticker"].astype(str)
            )
            if prior is not None and len(prior) == 10 and len(current) == 10:
                transitions += 1
                replacements.append(len(current - prior))
            prior = current
        rows.append(
            {
                "variant": variant,
                "valid_transitions": transitions,
                "mean_replacements": (
                    float(np.mean(replacements)) if replacements else np.nan
                ),
                "median_replacements": (
                    float(np.median(replacements)) if replacements else np.nan
                ),
                "mean_replacement_rate": (
                    float(np.mean(replacements) / 10.0)
                    if replacements else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    paths = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    if not v2["manifest"].exists():
        raise SystemExit(f"Missing completed V2 manifest: {v2['manifest']}")
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete")
    if any(
        bool(value)
        for value in manifest.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("V2 manifest enables an execution capability")

    historical_path = _resolve_manifest_path(
        root,
        manifest.get("input_artifacts", {}).get("historical panel"),
        "historical panel",
    )
    duration_path = paths["duration_winners"]
    duration_summary_path = paths["duration_summary"]
    required = {
        "historical panel": historical_path,
        "TTM duration winners": duration_path,
        "TTM duration summary": duration_summary_path,
        "V2 research manifest": v2["manifest"],
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing historical TTM replay input(s):\n  "
            + "\n  ".join(missing)
        )

    duration_summary = json.loads(
        duration_summary_path.read_text(encoding="utf-8")
    )
    if duration_summary.get("status") != "TTM_DURATION_CACHE_COMPLETE":
        raise SystemExit("V2 TTM duration cache is not complete")
    if int(duration_summary.get("unresolved_winner_groups", 1)) != 0:
        raise SystemExit("V2 TTM duration cache has unresolved winners")

    if paths["ttm_history_replay_dir"].exists():
        raise SystemExit(
            "Historical TTM replay output already exists; preserve or rename "
            f"it before rerun: {paths['ttm_history_replay_dir']}"
        )

    print("V2 HISTORICAL TTM VALUATION REPLAY")
    print(f"Research as-of:             {args.as_of.isoformat()}")
    print(f"Historical panel:           {historical_path}")
    print(f"Duration winners:           {duration_path}")

    ttm_events, event_summary = _event_cache(
        root=root,
        duration_path=duration_path,
        paths=paths,
        rebuild=args.rebuild_events,
    )

    print("Loading frozen historical V1 research panel...", flush=True)
    panel = pd.read_csv(historical_path, low_memory=False)
    if panel.duplicated(["decision_date", "ticker"]).any():
        raise SystemExit("Historical panel has duplicate decision_date/ticker")
    print(f"Historical rows:            {len(panel):,}")
    print(
        f"Historical decision dates:  {panel['decision_date'].nunique():,}"
    )

    print("Replaying TTM numerator states through decision dates...", flush=True)
    replay = replay_ttm_numerators_to_panel(panel, ttm_events)
    pit_audit = audit_historical_ttm_pit(replay)
    if not pit_audit.empty:
        raise SystemExit(
            f"Historical TTM replay produced {len(pit_audit)} PIT violations"
        )

    print("Scoring frozen annual valuation baseline...", flush=True)
    annual = add_valuation_factors(panel)
    annual = validate_raw_factors(annual)
    annual = normalize_validated_factors(annual)
    annual = score_weighted_family(
        annual,
        weights=ANNUAL_VALUATION_WEIGHTS,
        output_prefix="annual_valuation",
        minimum_factors=2,
    )

    print("Scoring historical TTM valuation family...", flush=True)
    ttm_scored = _score_historical_ttm(panel, replay, annual)

    coverage = _coverage_by_year(annual, ttm_scored)
    correlations = _correlations_by_year(annual, ttm_scored)
    rank, weekly = _rank_and_weekly_summary(annual, ttm_scored)
    turnover = _turnover(rank)

    detail_cols = [
        "decision_date", "ticker", "cik", "company_name", "return_price",
        "close", "shares_outstanding",
        "earnings_yield_annual", "earnings_yield_annual_validated",
        "earnings_yield_annual_score",
        "sales_yield_annual", "sales_yield_annual_validated",
        "sales_yield_annual_score",
        "free_cash_flow_yield_annual",
        "free_cash_flow_yield_annual_validated",
        "free_cash_flow_yield_annual_score",
        "book_to_market", "book_to_market_validated", "book_to_market_score",
        "annual_valuation_factor_count", "annual_valuation_eligible",
        "annual_valuation_score",
    ]
    annual_detail = annual[
        [c for c in detail_cols if c in annual.columns]
    ].copy()
    ttm_cols = [
        "decision_date", "ticker", "cik",
        "ttm_revenue", "ttm_net_income", "ttm_free_cash_flow",
        "earnings_yield_ttm", "earnings_yield_ttm_validated",
        "earnings_yield_ttm_score",
        "sales_yield_ttm", "sales_yield_ttm_validated",
        "sales_yield_ttm_score",
        "free_cash_flow_yield_ttm",
        "free_cash_flow_yield_ttm_validated",
        "free_cash_flow_yield_ttm_score",
        "ttm_valuation_factor_count", "ttm_valuation_eligible",
        "ttm_valuation_score",
    ]
    factor_panel = annual_detail.merge(
        ttm_scored[[c for c in ttm_cols if c in ttm_scored.columns]],
        on=["decision_date", "ticker", "cik"],
        how="left",
        validate="one_to_one",
    )

    overlap = weekly["overlap_scored"].sum()
    summary = {
        "schema_version": 1,
        "status": "TTM_HISTORICAL_VALUATION_REPLAY_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "scope": "valuation_family_only_diagnostic",
        "income_quarter_policy": "ytd_preferred",
        "cash_flow_endpoint_policy": "latest_common_endpoint",
        "historical_rows": len(panel),
        "historical_decision_dates": int(panel["decision_date"].nunique()),
        "quarter_event_rows": int(event_summary["quarter_event_rows"]),
        "ttm_event_rows": int(event_summary["ttm_event_rows"]),
        "pit_violations": len(pit_audit),
        "annual_valuation_eligible_rows": int(
            annual["annual_valuation_eligible"].sum()
        ),
        "ttm_valuation_eligible_rows": int(
            ttm_scored["ttm_valuation_eligible"].sum()
        ),
        "weekly_mean_top10_overlap": float(weekly["top10_overlap"].mean()),
        "weekly_median_top10_overlap": float(weekly["top10_overlap"].median()),
        "weekly_mean_family_spearman": float(
            weekly["family_score_spearman"].mean()
        ),
        "weekly_median_absolute_rank_change": float(
            weekly["median_absolute_rank_change"].median()
        ),
        "full_model_score_built": False,
        "performance_claim_made": False,
        "diagnostic_only": True,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    out = paths["ttm_history_replay_dir"]
    out.mkdir(parents=True, exist_ok=False)
    replay.to_csv(paths["ttm_history_numerators"], index=False)
    factor_panel.to_csv(paths["ttm_history_factor_panel"], index=False)
    coverage.to_csv(paths["ttm_history_coverage_by_year"], index=False)
    correlations.to_csv(
        paths["ttm_history_correlations_by_year"], index=False
    )
    rank.to_csv(paths["ttm_history_weekly_rank_comparison"], index=False)
    weekly.to_csv(paths["ttm_history_weekly_summary"], index=False)
    turnover.to_csv(paths["ttm_history_turnover_summary"], index=False)
    pit_audit.to_csv(paths["ttm_history_pit_audit"], index=False)
    paths["ttm_history_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["ttm_history_fingerprints"].write_text(
        json.dumps(
            {
                "schema_version": 1,
                "direct_inputs": fingerprint_files(
                    root=root, paths=list(required.values())
                ),
                "event_cache_summary": event_summary,
                "code": git_provenance(root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 HISTORICAL TTM VALUATION REPLAY COMPLETE")
    print(f"Historical rows:            {len(panel):,}")
    print(
        f"Decision dates:             {panel['decision_date'].nunique():,}"
    )
    print(
        "Annual valuation eligible: "
        f"{int(annual['annual_valuation_eligible'].sum()):,}"
    )
    print(
        "TTM valuation eligible:    "
        f"{int(ttm_scored['ttm_valuation_eligible'].sum()):,}"
    )
    print(f"PIT violations:             {len(pit_audit):,}")
    print(
        "Mean / median Top10 overlap:"
        f" {weekly['top10_overlap'].mean():.2f} / "
        f"{weekly['top10_overlap'].median():.1f}"
    )
    print(
        "Mean family Spearman:       "
        f"{weekly['family_score_spearman'].mean():.4f}"
    )
    print(
        "Median weekly rank shift:   "
        f"{weekly['median_absolute_rank_change'].median():.1f}"
    )
    print(f"Output directory:           {out}")
    print("NO FULL V2 MODEL SCORE OR PERFORMANCE CLAIM WAS BUILT.")
    print("V1 FACTOR VALUES AND MODEL OUTPUTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
