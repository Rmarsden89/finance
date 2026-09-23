from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.pit_reconciliation import eastern_timestamp
from finance.research.ttm_valuation import build_ttm_values
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build current PIT-safe TTM numerators from the enriched V2 "
            "discrete-quarter reconstruction. Research-only; no scoring."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _decision_cutoff(current_snapshot: pd.DataFrame, as_of: date) -> pd.Timestamp:
    rows = current_snapshot.loc[
        pd.to_datetime(
            current_snapshot["decision_date"], errors="coerce"
        ).dt.date.eq(as_of),
        "as_of",
    ]
    if rows.empty:
        raise SystemExit(
            f"Current V2 snapshot has no rows for {as_of.isoformat()}"
        )
    cutoffs = rows.map(eastern_timestamp)
    if cutoffs.isna().any() or cutoffs.nunique() != 1:
        raise SystemExit("Current V2 snapshot has invalid/multiple cutoffs")
    return cutoffs.iloc[0]


def _latest_ttm(values: pd.DataFrame) -> pd.DataFrame:
    if values.empty:
        return values.copy()
    ordered = values.sort_values(
        ["cik", "concept", "ttm_end_date", "available_at"],
        kind="stable",
    )
    return (
        ordered.groupby(["cik", "concept"], sort=False, as_index=False)
        .tail(1)
        .sort_values(["cik", "concept"], kind="stable")
        .reset_index(drop=True)
    )


def _latest_common_cash_flow(
    ttm_values: pd.DataFrame,
) -> pd.DataFrame:
    """Return the latest endpoint jointly available for OCF and capex."""

    ocf = ttm_values.loc[
        ttm_values["concept"].eq("operating_cash_flow")
    ].copy()
    capex = ttm_values.loc[
        ttm_values["concept"].eq("capital_expenditures")
    ].copy()

    keys = [
        "cik",
        "uom",
        "ttm_end_fy",
        "ttm_end_quarter",
        "ttm_end_date",
    ]
    merged = ocf.merge(
        capex,
        on=keys,
        how="inner",
        suffixes=("_ocf", "_capex"),
        validate="one_to_one",
    )
    if merged.empty:
        return pd.DataFrame(
            columns=[
                *keys,
                "ttm_operating_cash_flow",
                "ttm_capital_expenditures",
                "ttm_free_cash_flow",
                "available_at",
            ]
        )

    merged["ttm_operating_cash_flow"] = pd.to_numeric(
        merged["ttm_value_ocf"], errors="coerce"
    )
    merged["ttm_capital_expenditures"] = pd.to_numeric(
        merged["ttm_value_capex"], errors="coerce"
    )
    merged["ttm_free_cash_flow"] = (
        merged["ttm_operating_cash_flow"]
        - merged["ttm_capital_expenditures"]
    )
    merged["available_at"] = pd.concat(
        [
            pd.to_datetime(merged["available_at_ocf"], errors="coerce"),
            pd.to_datetime(merged["available_at_capex"], errors="coerce"),
        ],
        axis=1,
    ).max(axis=1)
    common = merged[
        [
            *keys,
            "ttm_operating_cash_flow",
            "ttm_capital_expenditures",
            "ttm_free_cash_flow",
            "available_at",
        ]
    ].copy()
    return (
        common.sort_values(
            ["cik", "ttm_end_date", "available_at"],
            kind="stable",
        )
        .groupby("cik", sort=False, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _current_numerators(
    current_snapshot: pd.DataFrame,
    latest_ttm: pd.DataFrame,
    ttm_values: pd.DataFrame,
) -> pd.DataFrame:
    universe = current_snapshot[
        ["ticker", "cik", "company_name"]
    ].drop_duplicates("ticker").copy()

    simple = latest_ttm.loc[
        latest_ttm["concept"].isin({"revenue", "net_income"})
    ].copy()
    if simple.empty:
        pivot = pd.DataFrame(columns=["cik"])
    else:
        pivot = simple.pivot(
            index="cik",
            columns="concept",
            values="ttm_value",
        ).reset_index()
        pivot = pivot.rename(
            columns={
                "revenue": "ttm_revenue",
                "net_income": "ttm_net_income",
            }
        )

    cash = _latest_common_cash_flow(ttm_values)

    result = universe.merge(pivot, on="cik", how="left")
    if not cash.empty:
        result = result.merge(
            cash[
                [
                    "cik",
                    "ttm_operating_cash_flow",
                    "ttm_capital_expenditures",
                    "ttm_free_cash_flow",
                    "ttm_end_date",
                    "available_at",
                ]
            ].rename(
                columns={
                    "ttm_end_date": "ttm_cash_flow_end_date",
                    "available_at": "ttm_cash_flow_available_at",
                }
            ),
            on="cik",
            how="left",
        )
    else:
        for column in (
            "ttm_operating_cash_flow",
            "ttm_capital_expenditures",
            "ttm_free_cash_flow",
            "ttm_cash_flow_end_date",
            "ttm_cash_flow_available_at",
        ):
            result[column] = pd.NA

    return result.sort_values("ticker", kind="stable").reset_index(drop=True)


def _annual_comparison(
    current_snapshot: pd.DataFrame,
    current_ttm: pd.DataFrame,
) -> pd.DataFrame:
    annual_columns = [
        "ticker",
        "cik",
        "annual_revenue",
        "annual_net_income",
        "annual_operating_cash_flow",
        "annual_capital_expenditures",
    ]
    missing = [
        column for column in annual_columns
        if column not in current_snapshot.columns
    ]
    if missing:
        raise SystemExit(
            "Current V2 snapshot lacks annual comparison columns: "
            + ", ".join(missing)
        )

    annual = current_snapshot[annual_columns].drop_duplicates("ticker").copy()
    annual["annual_free_cash_flow"] = (
        pd.to_numeric(
            annual["annual_operating_cash_flow"], errors="coerce"
        )
        - pd.to_numeric(
            annual["annual_capital_expenditures"], errors="coerce"
        )
    )

    comparison = annual.merge(
        current_ttm,
        on=["ticker", "cik"],
        how="left",
        validate="one_to_one",
    )
    pairs = (
        ("revenue", "annual_revenue", "ttm_revenue"),
        ("net_income", "annual_net_income", "ttm_net_income"),
        (
            "operating_cash_flow",
            "annual_operating_cash_flow",
            "ttm_operating_cash_flow",
        ),
        (
            "capital_expenditures",
            "annual_capital_expenditures",
            "ttm_capital_expenditures",
        ),
        ("free_cash_flow", "annual_free_cash_flow", "ttm_free_cash_flow"),
    )
    for prefix, annual_col, ttm_col in pairs:
        annual_values = pd.to_numeric(
            comparison[annual_col], errors="coerce"
        )
        ttm_values = pd.to_numeric(
            comparison[ttm_col], errors="coerce"
        )
        comparison[f"{prefix}_both_present"] = (
            annual_values.notna() & ttm_values.notna()
        )
        comparison[f"{prefix}_ttm_minus_annual"] = (
            ttm_values - annual_values
        )
        comparison[f"{prefix}_ttm_to_annual"] = (
            ttm_values / annual_values.where(annual_values.ne(0))
        )

    return comparison


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "enriched discrete quarters": ttm["enriched_quarters"],
        "enriched reconstruction summary": ttm["enriched_summary"],
        "current V2 snapshot": v2["current_snapshot"],
        "V2 research manifest": v2["manifest"],
    }
    missing = [
        f"{name}: {path}" for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing required TTM validation input(s):\n  "
            + "\n  ".join(missing)
        )

    reconstruction_summary = json.loads(
        ttm["enriched_summary"].read_text(encoding="utf-8")
    )
    if reconstruction_summary.get("pit_violations") != 0:
        raise SystemExit("Enriched quarter reconstruction has PIT violations")
    if (
        reconstruction_summary.get("winner_source")
        != "v2_ttm_duration_cache"
    ):
        raise SystemExit(
            "TTM validation requires the enriched V2 duration-cache reconstruction"
        )
    if reconstruction_summary.get("income_quarter_policy") != "ytd_preferred":
        raise SystemExit(
            "TTM validation requires the YTD-preferred income-quarter policy"
        )

    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if any(
        bool(value)
        for value in manifest.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("V2 manifest enables an execution capability")

    if ttm["ttm_validation_dir"].exists():
        raise SystemExit(
            "TTM validation output already exists; preserve or rename it "
            f"before another run: {ttm['ttm_validation_dir']}"
        )

    current_snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    cutoff = _decision_cutoff(current_snapshot, args.as_of)
    quarters = pd.read_csv(ttm["enriched_quarters"], low_memory=False)

    print("V2 CURRENT TTM NUMERATOR VALIDATION", flush=True)
    print(f"Decision date:              {args.as_of.isoformat()}", flush=True)
    print(f"Decision cutoff (Eastern):  {cutoff.isoformat()}", flush=True)
    print(f"Discrete quarters:          {len(quarters):,}", flush=True)
    print("Building strict four-quarter TTM values...", flush=True)

    result = build_ttm_values(
        quarters,
        as_of=cutoff.tz_localize(None),
    )
    values = result.values
    audit = result.audit
    print(
        f"TTM construction complete:  values={len(values):,}, "
        f"audit={len(audit):,}",
        flush=True,
    )
    print("Selecting latest TTM values by concept...", flush=True)
    latest = _latest_ttm(values)
    print(f"Latest concept rows:        {len(latest):,}", flush=True)
    print("Building current-universe TTM numerators...", flush=True)
    current_ttm = _current_numerators(current_snapshot, latest, values)
    print("Comparing TTM numerators with annual V1 inputs...", flush=True)
    comparison = _annual_comparison(current_snapshot, current_ttm)

    rejection_summary = (
        audit.groupby(
            ["concept", "reason"], as_index=False, dropna=False
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["concept", "rows"],
            ascending=[True, False],
            kind="stable",
        )
        if not audit.empty
        else pd.DataFrame(columns=["concept", "reason", "rows"])
    )

    available_at = pd.to_datetime(values["available_at"], errors="coerce")
    pit_violations = int(
        available_at.gt(cutoff.tz_localize(None)).sum()
    )

    numerator_columns = [
        "ttm_revenue",
        "ttm_net_income",
        "ttm_operating_cash_flow",
        "ttm_capital_expenditures",
        "ttm_free_cash_flow",
    ]
    coverage = {
        column: int(
            pd.to_numeric(current_ttm[column], errors="coerce").notna().sum()
        )
        for column in numerator_columns
    }

    comparison_counts = {}
    for prefix in (
        "revenue",
        "net_income",
        "operating_cash_flow",
        "capital_expenditures",
        "free_cash_flow",
    ):
        comparison_counts[prefix] = int(
            comparison[f"{prefix}_both_present"].sum()
        )

    summary = {
        "schema_version": 1,
        "status": "CURRENT_TTM_NUMERATOR_VALIDATION_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "decision_cutoff_eastern": cutoff.isoformat(),
        "income_quarter_policy": reconstruction_summary.get(
            "income_quarter_policy"
        ),
        "discrete_quarter_rows": len(quarters),
        "ttm_value_rows": len(values),
        "ttm_audit_rows": len(audit),
        "latest_ttm_rows": len(latest),
        "current_universe_rows": len(current_snapshot),
        "current_ttm_coverage": coverage,
        "annual_vs_ttm_comparable_rows": comparison_counts,
        "pit_violations": pit_violations,
        "annual_fallback_used": False,
        "valuation_scores_changed": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    ttm["ttm_validation_dir"].mkdir(parents=True, exist_ok=False)
    values.to_csv(ttm["ttm_values"], index=False)
    latest.to_csv(ttm["ttm_latest_by_concept"], index=False)
    current_ttm.to_csv(ttm["ttm_current_numerators"], index=False)
    comparison.to_csv(ttm["ttm_annual_comparison"], index=False)
    audit.to_csv(ttm["ttm_construction_audit"], index=False)
    rejection_summary.to_csv(ttm["ttm_rejection_summary"], index=False)
    ttm["ttm_validation_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "direct_inputs": fingerprint_files(
            root=root,
            paths=[
                ttm["enriched_quarters"],
                ttm["enriched_summary"],
                v2["current_snapshot"],
                v2["manifest"],
            ],
        ),
        "code": git_provenance(root),
    }
    ttm["ttm_validation_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 CURRENT TTM NUMERATOR VALIDATION COMPLETE")
    print(f"TTM value rows:             {len(values):,}")
    print(f"TTM audit rows:             {len(audit):,}")
    print(f"Latest concept rows:        {len(latest):,}")
    print(
        "Current TTM coverage:       "
        + ", ".join(
            f"{name.replace('ttm_', '')}={count:,}"
            for name, count in coverage.items()
        )
    )
    print(f"PIT violations:             {pit_violations}")
    print(f"Output directory:           {ttm['ttm_validation_dir']}")
    print("ANNUAL FALLBACK WAS NOT USED.")
    print("VALUATION SCORES WERE NOT BUILT OR MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
