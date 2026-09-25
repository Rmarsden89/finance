from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from finance.research.missingness_bias import compare_variants
from finance.research.pit_reconciliation import eastern_timestamp
from finance.research.share_historical_validation import (
    build_quarter_share_candidates,
    select_pit_share_candidate,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths
from finance.research.v2_impact import score_long_growth_panel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the research-only SEC DEI shares candidate across the "
            "frozen historical panel and measure coverage/model impact."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--pattern", default="*.zip")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna("").astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )


def _decision_cutoff(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(
            "America/New_York",
            ambiguous="raise",
            nonexistent="raise",
        )
    return stamp.tz_convert("America/New_York")


def _coverage_by_year(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> pd.DataFrame:
    left = baseline.copy()
    right = challenger.copy()
    left["decision_date"] = pd.to_datetime(left["decision_date"], errors="coerce")
    right["decision_date"] = pd.to_datetime(right["decision_date"], errors="coerce")
    left["year"] = left["decision_date"].dt.year
    right["year"] = right["decision_date"].dt.year
    left["_present"] = pd.to_numeric(
        left["shares_outstanding"], errors="coerce"
    ).gt(0)
    right["_present"] = pd.to_numeric(
        right["shares_outstanding"], errors="coerce"
    ).gt(0)
    base = left.groupby("year", as_index=False).agg(
        rows=("ticker", "size"),
        baseline_shares_present=("_present", "sum"),
    )
    chal = right.groupby("year", as_index=False).agg(
        v3_shares_present=("_present", "sum"),
    )
    out = base.merge(chal, on="year", how="outer", validate="one_to_one")
    out["baseline_coverage"] = out["baseline_shares_present"] / out["rows"]
    out["v3_coverage"] = out["v3_shares_present"] / out["rows"]
    out["coverage_gain_percentage_points"] = (
        out["v3_coverage"] - out["baseline_coverage"]
    ) * 100
    return out.sort_values("year", kind="stable")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    zip_dir = args.zip_dir.resolve()
    zip_paths = sorted(zip_dir.glob(args.pattern))
    if not zip_paths:
        raise SystemExit(f"No SEC ZIPs found in {zip_dir} matching {args.pattern!r}")

    print("V3 HISTORICAL RAW-SEC SHARES REPLAY", flush=True)
    print(f"As of:                       {args.as_of.isoformat()}", flush=True)
    print(f"SEC ZIP directory:           {zip_dir}", flush=True)
    print(f"SEC ZIPs:                    {len(zip_paths)}", flush=True)

    started = time.monotonic()
    quarters: list[pd.DataFrame] = []
    for index, path in enumerate(zip_paths, start=1):
        print(
            f"[{index}/{len(zip_paths)}] {path.name} "
            f"elapsed={(time.monotonic()-started)/60:.1f}m",
            flush=True,
        )
        frame = build_quarter_share_candidates(path)
        if not frame.empty:
            quarters.append(frame)
        print(f"    filing candidates={len(frame):,}", flush=True)

    filings = (
        pd.concat(quarters, ignore_index=True)
        if quarters
        else pd.DataFrame()
    )
    if filings.empty:
        raise SystemExit("No historical DEI share filing candidates were found")

    filings["cik"] = pd.to_numeric(filings["cik"], errors="coerce").astype("Int64")
    filings = filings.drop_duplicates(
        ["adsh", "cik", "context_instant", "selection_rule"],
        keep="last",
    ).reset_index(drop=True)

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("V2 manifest lacks historical panel input")
    historical_path = Path(historical_value)
    if not historical_path.is_absolute():
        historical_path = root / historical_path
    if not historical_path.exists():
        raise SystemExit(f"Missing frozen historical panel: {historical_path}")

    baseline = pd.read_csv(historical_path, low_memory=False)
    baseline["decision_date"] = pd.to_datetime(
        baseline["decision_date"], errors="coerce"
    ).dt.normalize()
    baseline["cik"] = pd.to_numeric(baseline["cik"], errors="coerce").astype("Int64")
    if "as_of" not in baseline.columns:
        raise SystemExit("Historical panel missing as_of PIT cutoff")
    baseline["decision_cutoff"] = baseline["as_of"].map(_decision_cutoff)

    facts_by_cik = {
        int(cik): group.sort_values(
            ["available_at", "context_instant", "accepted_at", "adsh"],
            kind="stable",
        ).reset_index(drop=True)
        for cik, group in filings.loc[filings["cik"].notna()].groupby("cik")
    }

    replay_rows: list[dict[str, object]] = []
    for row in baseline[
        ["decision_date", "ticker", "cik", "decision_cutoff"]
    ].itertuples(index=False):
        if pd.isna(row.cik) or pd.isna(row.decision_cutoff):
            continue
        issuer = facts_by_cik.get(int(row.cik))
        if issuer is None:
            continue
        selected = select_pit_share_candidate(
            issuer,
            decision_cutoff=row.decision_cutoff,
            decision_date=row.decision_date,
        )
        if selected is None:
            continue
        replay_rows.append(
            {
                "decision_date": row.decision_date,
                "ticker": row.ticker,
                "cik": int(row.cik),
                "decision_cutoff": row.decision_cutoff,
                "candidate_shares": selected["candidate_shares"],
                "selection_rule": selected["selection_rule"],
                "selected_accession": selected["adsh"],
                "selected_context_instant": selected["context_instant"],
                "selected_accepted_at": selected["accepted_at"],
                "selected_available_at": selected["available_at"],
                "source_zip": selected["source_zip"],
            }
        )

    replay = pd.DataFrame(replay_rows)
    if replay.empty:
        raise SystemExit("Historical PIT replay produced no share candidates")

    challenger = baseline.merge(
        replay,
        on=["decision_date", "ticker", "cik"],
        how="left",
        validate="one_to_one",
    )
    # Snapshot the baseline shares before applying any recovery.  Keep this
    # detached from the challenger column so later assignments cannot change
    # the baseline coverage/overlap diagnostics through a shared pandas view.
    original = pd.to_numeric(
        challenger["shares_outstanding"], errors="coerce"
    ).copy()
    candidate = pd.to_numeric(
        challenger["candidate_shares"], errors="coerce"
    ).copy()
    apply = ~original.gt(0) & candidate.gt(0)
    challenger["v3_raw_share_rule_applied"] = apply
    challenger["baseline_shares_outstanding"] = original
    challenger.loc[apply, "shares_outstanding"] = candidate.loc[apply]

    # score_long_growth_panel recomputes market cap from close * shares.
    baseline_scored = score_long_growth_panel(baseline)
    challenger_scored = score_long_growth_panel(challenger)

    coverage = _coverage_by_year(baseline, challenger)
    (
        impact_detail,
        weekly_top10,
        turnover,
        rank_displacement,
        concentration,
    ) = compare_variants(baseline_scored, challenger_scored)

    base_valuation = pd.to_numeric(
        baseline_scored["valuation_score"], errors="coerce"
    ).notna()
    v3_valuation = pd.to_numeric(
        challenger_scored["valuation_score"], errors="coerce"
    ).notna()
    base_top = _bool(baseline_scored["top_conviction_eligible"])
    v3_top = _bool(challenger_scored["top_conviction_eligible"])

    valid_turnover = turnover.loc[_bool(turnover["comparison_valid"])].copy()
    turnover_means = valid_turnover.groupby("variant")["replacement_rate"].mean()
    baseline_turnover = float(turnover_means.get("baseline", np.nan))
    v3_turnover = float(turnover_means.get("challenger", np.nan))

    populated = weekly_top10.loc[
        weekly_top10["baseline_count"].eq(10)
        & weekly_top10["challenger_count"].eq(10)
    ].copy()

    applied = challenger.loc[apply].copy()
    applied["year"] = applied["decision_date"].dt.year
    recovery_by_year = (
        applied.groupby("year", as_index=False)
        .agg(
            recovery_rows=("ticker", "size"),
            recovered_issuers=("cik", "nunique"),
            recovered_tickers=("ticker", "nunique"),
            decision_dates=("decision_date", "nunique"),
        )
        .sort_values("year", kind="stable")
    )

    selection_summary = (
        applied.groupby("selection_rule", as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values("selection_rule", kind="stable")
    )

    overlap = challenger.loc[
        original.gt(0) & candidate.gt(0),
        [
            "decision_date",
            "ticker",
            "cik",
            "baseline_shares_outstanding",
            "candidate_shares",
            "selection_rule",
            "selected_context_instant",
        ],
    ].copy()
    overlap["absolute_relative_error"] = (
        (
            pd.to_numeric(overlap["candidate_shares"], errors="coerce")
            - pd.to_numeric(
                overlap["baseline_shares_outstanding"], errors="coerce"
            )
        ).abs()
        / pd.to_numeric(
            overlap["baseline_shares_outstanding"], errors="coerce"
        )
    )
    overlap["validation_band"] = "material_difference"
    overlap.loc[
        overlap["absolute_relative_error"].le(0.01),
        "validation_band",
    ] = "within_1_pct"
    overlap.loc[
        overlap["absolute_relative_error"].le(0.001),
        "validation_band",
    ] = "within_0_1_pct"
    overlap.loc[
        overlap["absolute_relative_error"].le(0.0001),
        "validation_band",
    ] = "within_0_01_pct"
    overlap.loc[
        overlap["absolute_relative_error"].eq(0),
        "validation_band",
    ] = "exact_match"

    pit_available = pd.to_datetime(
        replay["selected_available_at"], errors="coerce", utc=True
    )
    pit_cutoff = pd.to_datetime(
        replay["decision_cutoff"], errors="coerce", utc=True
    )
    pit_violations = int((pit_available > pit_cutoff).fillna(False).sum())

    summary = {
        "as_of": args.as_of.isoformat(),
        "zip_count": len(zip_paths),
        "historical_rows": int(len(baseline)),
        "historical_decision_dates": int(baseline["decision_date"].nunique()),
        "filing_candidates": int(len(filings)),
        "candidate_ciks": int(filings["cik"].nunique()),
        "pit_replay_rows": int(len(replay)),
        "recovery_rows_applied": int(apply.sum()),
        "recovery_issuers_applied": int(applied["cik"].nunique()),
        "recovery_decision_dates": int(applied["decision_date"].nunique()),
        "baseline_shares_present": int(original.gt(0).sum()),
        "v3_shares_present": int(
            pd.to_numeric(
                challenger["shares_outstanding"], errors="coerce"
            ).gt(0).sum()
        ),
        "valuation_eligibility_gained_rows": int(
            (~base_valuation & v3_valuation).sum()
        ),
        "valuation_eligibility_lost_rows": int(
            (base_valuation & ~v3_valuation).sum()
        ),
        "top_conviction_gained_rows": int((~base_top & v3_top).sum()),
        "top_conviction_lost_rows": int((base_top & ~v3_top).sum()),
        "populated_top10_dates": int(len(populated)),
        "mean_top10_overlap": (
            float(populated["overlap_count"].mean())
            if not populated.empty else None
        ),
        "latest_top10_overlap": (
            int(populated.iloc[-1]["overlap_count"])
            if not populated.empty else None
        ),
        "baseline_mean_weekly_replacement_rate": (
            None if np.isnan(baseline_turnover) else baseline_turnover
        ),
        "v3_mean_weekly_replacement_rate": (
            None if np.isnan(v3_turnover) else v3_turnover
        ),
        "replacement_rate_delta": (
            None
            if np.isnan(baseline_turnover) or np.isnan(v3_turnover)
            else v3_turnover - baseline_turnover
        ),
        "rank_displacement_rows": int(len(rank_displacement)),
        "median_absolute_rank_displacement": (
            float(rank_displacement["absolute_rank_displacement"].median())
            if not rank_displacement.empty else None
        ),
        "max_absolute_rank_displacement": (
            float(rank_displacement["absolute_rank_displacement"].max())
            if not rank_displacement.empty else None
        ),
        "overlap_rows": int(len(overlap)),
        "overlap_material_differences": int(
            overlap["validation_band"].eq("material_difference").sum()
        ),
        "pit_violations": pit_violations,
        "availability_policy": (
            "max(sec_acceptance_timestamp, filed_date_0600_America_New_York)"
        ),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "paid_vendor_used": False,
        "broker_capability": False,
        "order_capability": False,
    }

    output_dir = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "shares_historical_replay"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": output_dir / "summary.json",
        "filings": output_dir / "filing_candidates.csv",
        "replay": output_dir / "pit_replay_detail.csv",
        "recovery": output_dir / "recovery_detail.csv",
        "recovery_year": output_dir / "recovery_by_year.csv",
        "coverage": output_dir / "coverage_by_year.csv",
        "selection": output_dir / "selection_rule_summary.csv",
        "overlap": output_dir / "canonical_overlap.csv",
        "top10": output_dir / "weekly_top10_comparison.csv",
        "turnover": output_dir / "weekly_top10_turnover.csv",
        "rank": output_dir / "rank_displacement.csv",
    }

    filings.to_csv(paths["filings"], index=False)
    replay.to_csv(paths["replay"], index=False)
    applied.to_csv(paths["recovery"], index=False)
    recovery_by_year.to_csv(paths["recovery_year"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    selection_summary.to_csv(paths["selection"], index=False)
    overlap.to_csv(paths["overlap"], index=False)
    weekly_top10.to_csv(paths["top10"], index=False)
    turnover.to_csv(paths["turnover"], index=False)
    rank_displacement.to_csv(paths["rank"], index=False)
    paths["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V3 HISTORICAL RAW-SEC SHARES REPLAY COMPLETE")
    print(f"Filing candidates:           {summary['filing_candidates']}")
    print(f"Candidate CIKs:              {summary['candidate_ciks']}")
    print(f"PIT replay rows:             {summary['pit_replay_rows']}")
    print(f"Recovery rows applied:       {summary['recovery_rows_applied']}")
    print(
        f"Recovery issuers/dates:      "
        f"{summary['recovery_issuers_applied']}/"
        f"{summary['recovery_decision_dates']}"
    )
    print(
        f"Shares present:              "
        f"{summary['baseline_shares_present']} -> "
        f"{summary['v3_shares_present']}"
    )
    print(
        f"Valuation gained/lost:       "
        f"{summary['valuation_eligibility_gained_rows']}/"
        f"{summary['valuation_eligibility_lost_rows']}"
    )
    print(
        f"Top-conviction gained/lost:  "
        f"{summary['top_conviction_gained_rows']}/"
        f"{summary['top_conviction_lost_rows']}"
    )
    print(
        f"Top-10 mean/latest overlap:  "
        f"{summary['mean_top10_overlap']}/"
        f"{summary['latest_top10_overlap']}"
    )
    print(
        f"Mean replacement rate:       "
        f"{summary['baseline_mean_weekly_replacement_rate']} -> "
        f"{summary['v3_mean_weekly_replacement_rate']}"
    )
    print(
        f"Median/max rank displacement:"
        f" {summary['median_absolute_rank_displacement']}/"
        f"{summary['max_absolute_rank_displacement']}"
    )
    print(
        f"Canonical overlap/material:  "
        f"{summary['overlap_rows']}/"
        f"{summary['overlap_material_differences']}"
    )
    print(f"PIT violations:              {summary['pit_violations']}")
    print(f"Summary:                     {paths['summary']}")
    print(f"Recovery by year:            {paths['recovery_year']}")
    print(f"Coverage by year:            {paths['coverage']}")
    print(f"Selection rules:             {paths['selection']}")
    print(f"Canonical overlap:           {paths['overlap']}")
    print("NO V1 OR V2 INPUTS, SCORES, OR RULES WERE MODIFIED.")
    print("NO PAID VENDOR, BROKER, OR ORDER CAPABILITY IS USED.")


if __name__ == "__main__":
    main()
