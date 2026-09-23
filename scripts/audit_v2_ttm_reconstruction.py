from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.pit_reconciliation import eastern_timestamp
from finance.research.ttm_valuation import (
    DURATION_CONCEPTS,
    reconstruct_discrete_quarters_as_of,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit PIT-safe SEC discrete-quarter reconstruction for the "
            "Issue #6 TTM valuation challenger. Read-only research only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve_input(root: Path, value: object, label: str) -> Path:
    if not value:
        raise SystemExit(f"Completed V2 manifest lacks {label}")
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise SystemExit(f"Missing {label}: {path}")
    return path


def _verified_manifest_inputs(
    root: Path,
    manifest: dict[str, object],
) -> tuple[Path, Path]:
    artifacts = manifest.get("input_artifacts", {})
    if not isinstance(artifacts, dict):
        raise SystemExit("Completed V2 manifest has invalid input_artifacts")

    historical_path = _resolve_input(
        root, artifacts.get("historical panel"), "historical panel"
    )
    sec_path = _resolve_input(
        root,
        artifacts.get("historical SEC winners"),
        "historical SEC winners",
    )

    groups = (
        manifest.get("input_fingerprints", {})
        .get("groups", {})
    )
    for name, path, group_name in (
        ("historical panel", historical_path, "historical_panel"),
        ("historical SEC winners", sec_path, "historical_sec"),
    ):
        expected = groups.get(group_name, {}).get("sha256")
        if not expected:
            raise SystemExit(
                f"Completed V2 manifest lacks fingerprint for {name}"
            )
        actual = fingerprint_files(root=root, paths=[path])["sha256"]
        if actual != expected:
            raise SystemExit(
                f"{name} differs from completed V2 research input"
            )
    return historical_path, sec_path


def _decision_cutoff(
    panel_path: Path,
    as_of: date,
    *,
    label: str,
) -> pd.Timestamp:
    if not panel_path.exists():
        raise SystemExit(f"Missing {label}: {panel_path}")
    header = pd.read_csv(panel_path, nrows=0)
    required = {"decision_date", "as_of"}
    missing = sorted(required - set(header.columns))
    if missing:
        raise SystemExit(
            f"{label} lacks cutoff columns: {', '.join(missing)}"
        )
    frame = pd.read_csv(
        panel_path,
        usecols=["decision_date", "as_of"],
        low_memory=False,
    )
    dates = pd.to_datetime(
        frame["decision_date"], errors="coerce"
    ).dt.date
    rows = frame.loc[dates.eq(as_of), "as_of"]
    if rows.empty:
        raise SystemExit(
            f"{label} has no decision rows for {as_of.isoformat()}"
        )
    cutoffs = rows.map(eastern_timestamp)
    if cutoffs.isna().any():
        raise SystemExit(f"{label} has invalid decision cutoff values")
    unique = pd.Index(cutoffs.unique())
    if len(unique) != 1:
        raise SystemExit(
            f"{label} has multiple decision cutoffs for the requested date"
        )
    return cutoffs.iloc[0]


def _eligible_duration_facts(
    winners: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    facts = winners.copy().reset_index(drop=True)
    facts["_input_order"] = range(len(facts))
    facts["accepted_at"] = facts["accepted_at"].map(eastern_timestamp)
    facts["fy"] = pd.to_numeric(facts["fy"], errors="coerce")
    facts["qtrs"] = pd.to_numeric(facts["qtrs"], errors="coerce")
    facts["fp"] = facts["fp"].astype(str).str.upper().str.strip()
    facts["uom"] = facts["uom"].astype(str).str.upper().str.strip()
    return facts.loc[
        facts["concept"].isin(DURATION_CONCEPTS)
        & facts["accepted_at"].notna()
        & facts["accepted_at"].le(cutoff)
        & facts["fy"].notna()
        & facts["fp"].isin({"Q1", "Q2", "Q3", "FY"})
        & facts["qtrs"].isin({1, 2, 3, 4})
        & facts["uom"].ne("")
    ].copy()


def _amendment_groups(facts: pd.DataFrame) -> pd.DataFrame:
    keys = ["cik", "concept", "fy", "fp", "qtrs", "uom"]
    rows: list[dict[str, object]] = []
    for key, group in facts.groupby(keys, dropna=False, sort=False):
        accepted = group["accepted_at"].dropna()
        if accepted.nunique() <= 1:
            continue
        values = dict(zip(keys, key))
        rows.append(
            {
                **values,
                "candidate_rows": len(group),
                "unique_acceptances": int(accepted.nunique()),
                "first_accepted_at": accepted.min().isoformat(),
                "latest_accepted_at": accepted.max().isoformat(),
                "forms": "|".join(
                    sorted(set(group["form"].astype(str)))
                ),
                "adshs": "|".join(
                    group.sort_values(
                        ["accepted_at", "_input_order"], kind="stable"
                    )["adsh"].astype(str).tolist()
                ),
                "source_tags": "|".join(
                    sorted(set(group["source_tag"].astype(str)))
                ),
            }
        )
    columns = [
        *keys,
        "candidate_rows",
        "unique_acceptances",
        "first_accepted_at",
        "latest_accepted_at",
        "forms",
        "adshs",
        "source_tags",
    ]
    return pd.DataFrame(rows, columns=columns)


def _group_coverage(
    facts: pd.DataFrame,
    quarters: pd.DataFrame,
) -> pd.DataFrame:
    keys = ["cik", "concept", "fy", "uom"]
    groups = facts[keys].drop_duplicates().copy()
    if groups.empty:
        return pd.DataFrame(
            columns=[
                *keys,
                "reconstructed_quarters",
                "full_four_quarters",
                "q1_available",
                "q2_available",
                "q3_available",
                "q4_available",
            ]
        )

    available = (
        quarters[keys + ["fiscal_quarter"]]
        .drop_duplicates()
        .assign(available=True)
        .pivot_table(
            index=keys,
            columns="fiscal_quarter",
            values="available",
            aggfunc="max",
            fill_value=False,
        )
        .reset_index()
        if not quarters.empty
        else pd.DataFrame(columns=keys)
    )
    result = groups.merge(available, on=keys, how="left")
    for quarter in ("Q1", "Q2", "Q3", "Q4"):
        if quarter not in result:
            result[quarter] = False
        result[quarter] = result[quarter].fillna(False).astype(bool)
    result["reconstructed_quarters"] = result[
        ["Q1", "Q2", "Q3", "Q4"]
    ].sum(axis=1)
    result["full_four_quarters"] = result["reconstructed_quarters"].eq(4)
    return result.rename(
        columns={
            "Q1": "q1_available",
            "Q2": "q2_available",
            "Q3": "q3_available",
            "Q4": "q4_available",
        }
    ).sort_values(keys, kind="stable").reset_index(drop=True)


def _coverage_by_year(group_coverage: pd.DataFrame) -> pd.DataFrame:
    if group_coverage.empty:
        return pd.DataFrame(
            columns=[
                "fy",
                "concept",
                "groups",
                "full_four_groups",
                "full_four_coverage_pct",
                "q1_coverage_pct",
                "q2_coverage_pct",
                "q3_coverage_pct",
                "q4_coverage_pct",
            ]
        )

    def summarize(frame: pd.DataFrame, concept: str) -> dict[str, object]:
        total = len(frame)
        return {
            "fy": int(frame["fy"].iloc[0]),
            "concept": concept,
            "groups": total,
            "full_four_groups": int(frame["full_four_quarters"].sum()),
            "full_four_coverage_pct": (
                float(frame["full_four_quarters"].mean()) if total else 0.0
            ),
            "q1_coverage_pct": float(frame["q1_available"].mean()),
            "q2_coverage_pct": float(frame["q2_available"].mean()),
            "q3_coverage_pct": float(frame["q3_available"].mean()),
            "q4_coverage_pct": float(frame["q4_available"].mean()),
        }

    rows: list[dict[str, object]] = []
    for fy, year_group in group_coverage.groupby("fy", sort=True):
        rows.append(summarize(year_group, "ALL"))
        for concept, concept_group in year_group.groupby(
            "concept", sort=True
        ):
            rows.append(summarize(concept_group, str(concept)))
    return pd.DataFrame(rows)


def _derivation_summary(quarters: pd.DataFrame) -> pd.DataFrame:
    if quarters.empty:
        return pd.DataFrame(
            columns=["concept", "fiscal_quarter", "derivation", "rows"]
        )
    return (
        quarters.groupby(
            ["concept", "fiscal_quarter", "derivation"],
            as_index=False,
            dropna=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["concept", "fiscal_quarter", "rows"],
            ascending=[True, True, False],
            kind="stable",
        )
    )


def _rejection_summary(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(
            columns=[
                "fy",
                "concept",
                "fiscal_quarter",
                "reason",
                "rows",
            ]
        )
    return (
        audit.groupby(
            ["fy", "concept", "fiscal_quarter", "reason"],
            as_index=False,
            dropna=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["fy", "concept", "fiscal_quarter", "rows"],
            ascending=[True, True, True, False],
            kind="stable",
        )
    )


def _fiscal_calendar_summary(quarters: pd.DataFrame) -> pd.DataFrame:
    q4 = quarters.loc[
        quarters["fiscal_quarter"].eq("Q4")
    ].copy()
    if q4.empty:
        return pd.DataFrame(
            columns=[
                "concept",
                "fiscal_year_end_month",
                "calendar_type",
                "rows",
            ]
        )
    q4["fiscal_year_end_month"] = pd.to_datetime(
        q4["quarter_end_date"], errors="coerce"
    ).dt.month
    q4["calendar_type"] = q4["fiscal_year_end_month"].map(
        lambda month: "calendar_year" if month == 12 else "non_calendar"
    )
    return (
        q4.groupby(
            ["concept", "fiscal_year_end_month", "calendar_type"],
            as_index=False,
            dropna=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["concept", "fiscal_year_end_month"], kind="stable"
        )
    )


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    output = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

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

    historical_path, sec_path = _verified_manifest_inputs(
        root, manifest
    )
    current_panel_path = v2["scoring_panel"]
    cutoff = _decision_cutoff(
        current_panel_path,
        args.as_of,
        label="Current V2 scoring panel",
    )

    if output["diagnostic_dir"].exists():
        raise SystemExit(
            "TTM diagnostic output already exists; preserve or rename it "
            f"before another run: {output['diagnostic_dir']}"
        )

    print("V2 TTM DISCRETE-QUARTER RECONSTRUCTION DIAGNOSTIC", flush=True)
    print(f"Decision date:              {args.as_of.isoformat()}", flush=True)
    print(f"Decision cutoff (Eastern):  {cutoff.isoformat()}", flush=True)
    print(f"Cutoff source panel:        {current_panel_path}", flush=True)
    print(f"Historical SEC winners:     {sec_path}", flush=True)
    print("Loading fingerprinted SEC winner cache...", flush=True)

    winners = pd.read_csv(sec_path, low_memory=False)
    eligible = _eligible_duration_facts(winners, cutoff)
    print(f"Eligible duration facts:    {len(eligible):,}", flush=True)
    print("Reconstructing PIT-visible discrete quarters...", flush=True)

    # The winner cache stores legacy naive acceptance timestamps as Eastern.
    reconstruction = reconstruct_discrete_quarters_as_of(
        winners,
        as_of=cutoff.tz_localize(None),
    )
    quarters = reconstruction.quarters
    audit = reconstruction.audit
    group_coverage = _group_coverage(eligible, quarters)
    coverage_by_year = _coverage_by_year(group_coverage)
    derivation_summary = _derivation_summary(quarters)
    rejection_summary = _rejection_summary(audit)
    amendments = _amendment_groups(eligible)
    calendar_summary = _fiscal_calendar_summary(quarters)

    available_at = pd.to_datetime(
        quarters["available_at"], errors="coerce"
    ) if not quarters.empty else pd.Series(dtype="datetime64[ns]")
    naive_cutoff = cutoff.tz_localize(None)
    pit_mask = available_at.gt(naive_cutoff)
    pit_audit = quarters.loc[
        pit_mask,
        [
            "cik",
            "concept",
            "fy",
            "fiscal_quarter",
            "available_at",
            "source_adshs",
        ],
    ].copy()
    if not pit_audit.empty:
        pit_audit["decision_cutoff"] = naive_cutoff.isoformat()
        pit_audit["status"] = "available_after_cutoff"

    direct_rows = int(
        quarters["derivation"].eq("direct_qtrs_1").sum()
    ) if not quarters.empty else 0
    derived_rows = len(quarters) - direct_rows
    full_groups = int(
        group_coverage["full_four_quarters"].sum()
    ) if not group_coverage.empty else 0
    non_calendar_q4 = (
        calendar_summary.loc[
            calendar_summary["calendar_type"].eq("non_calendar"),
            "rows",
        ].sum()
        if not calendar_summary.empty
        else 0
    )

    summary = {
        "schema_version": 1,
        "status": "TTM_RECONSTRUCTION_DIAGNOSTIC_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "decision_cutoff_eastern": cutoff.isoformat(),
        "historical_sec_winner_rows": len(winners),
        "eligible_duration_fact_rows": len(eligible),
        "duration_concepts": sorted(DURATION_CONCEPTS),
        "reconstructed_quarter_rows": len(quarters),
        "direct_quarter_rows": direct_rows,
        "derived_quarter_rows": derived_rows,
        "reconstruction_audit_rows": len(audit),
        "candidate_groups": len(group_coverage),
        "full_four_quarter_groups": full_groups,
        "full_four_quarter_group_pct": (
            full_groups / len(group_coverage)
            if len(group_coverage)
            else None
        ),
        "amendment_groups": len(amendments),
        "non_calendar_q4_rows": int(non_calendar_q4),
        "pit_violations": len(pit_audit),
        "ttm_values_built": False,
        "valuation_scores_changed": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    output["diagnostic_dir"].mkdir(parents=True, exist_ok=False)
    quarters.to_csv(output["quarters"], index=False)
    audit.to_csv(output["reconstruction_audit"], index=False)
    group_coverage.to_csv(output["group_coverage"], index=False)
    coverage_by_year.to_csv(
        output["coverage_by_fiscal_year"], index=False
    )
    derivation_summary.to_csv(output["derivation_summary"], index=False)
    rejection_summary.to_csv(output["rejection_summary"], index=False)
    amendments.to_csv(output["amendment_groups"], index=False)
    calendar_summary.to_csv(
        output["fiscal_calendar_summary"], index=False
    )
    pit_audit.to_csv(output["pit_audit"], index=False)
    output["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "decision_cutoff_eastern": cutoff.isoformat(),
        "source_research_fingerprint_bundle": manifest.get(
            "input_fingerprints", {}
        ).get("bundle_sha256"),
        "cutoff_source_panel": str(current_panel_path),
        "direct_inputs": fingerprint_files(
            root=root,
            paths=[
                historical_path,
                sec_path,
                current_panel_path,
                v2["manifest"],
            ],
        ),
        "diagnostic_code": git_provenance(root),
    }
    output["input_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 TTM RECONSTRUCTION DIAGNOSTIC COMPLETE")
    print(f"Reconstructed quarters:     {len(quarters):,}")
    print(f"Direct / derived:           {direct_rows:,} / {derived_rows:,}")
    print(
        "Full-four-quarter groups:  "
        f"{full_groups:,} / {len(group_coverage):,}"
    )
    print(f"Rejection audit rows:       {len(audit):,}")
    print(f"Amendment groups:           {len(amendments):,}")
    print(f"Non-calendar Q4 rows:       {int(non_calendar_q4):,}")
    print(f"PIT violations:             {len(pit_audit)}")
    print(f"Output directory:           {output['diagnostic_dir']}")
    print("TTM VALUES AND VALUATION SCORES WERE NOT BUILT OR MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
