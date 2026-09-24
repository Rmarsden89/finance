from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_sec_duration import build_ttm_duration_winners
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a V2-only SEC duration winner cache for TTM research from "
            "quarterly SEC Financial Statement Data Set ZIPs."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--zip-dir",
        type=Path,
        default=None,
        help=(
            "SEC quarterly ZIP directory. Defaults to "
            "data/raw/sec/financial_statements under the repo."
        ),
    )
    parser.add_argument(
        "--force-quarter-cache",
        action="store_true",
        help="Rebuild per-quarter V2 TTM cache files instead of reusing them.",
    )
    return parser.parse_args()


def _zip_paths(zip_dir: Path) -> list[Path]:
    paths = sorted(zip_dir.glob("*.zip"))
    if not paths:
        raise SystemExit(f"No SEC quarterly ZIP files found under {zip_dir}")
    return paths


def _restore_types(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("period_date", "filed_date", "ddate_date"):
        if column in result.columns:
            result[column] = pd.to_datetime(
                result[column], errors="coerce"
            ).dt.date
    if "accepted_at" in result.columns:
        result["accepted_at"] = pd.to_datetime(
            result["accepted_at"], errors="coerce"
        )
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    zip_dir = (
        args.zip_dir.resolve()
        if args.zip_dir is not None
        else root / "data" / "raw" / "sec" / "financial_statements"
    )

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    output = resolve_v2_ttm_diagnostic_paths(root, args.as_of)
    duration_dir = output["duration_cache_dir"]
    quarter_dir = output["duration_quarter_cache_dir"]

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

    if output["duration_winners"].exists() and not args.force_quarter_cache:
        raise SystemExit(
            "Combined V2 TTM duration cache already exists; preserve or remove "
            f"it before rebuilding: {output['duration_winners']}"
        )

    paths = _zip_paths(zip_dir)
    quarter_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    audit_frames: list[pd.DataFrame] = []
    quarter_summaries: list[dict[str, object]] = []
    processed = 0
    reused = 0

    print("V2 TTM DURATION CACHE BUILD", flush=True)
    print(f"SEC ZIP directory:          {zip_dir}", flush=True)
    print(f"Quarterly ZIPs found:       {len(paths)}", flush=True)

    for index, zip_path in enumerate(paths, start=1):
        winners_path = quarter_dir / f"{zip_path.stem}_ttm_duration_winners.csv"
        audit_path = quarter_dir / f"{zip_path.stem}_ttm_duration_winner_audit.csv"
        summary_path = quarter_dir / f"{zip_path.stem}_summary.json"

        if (
            winners_path.exists()
            and audit_path.exists()
            and summary_path.exists()
            and not args.force_quarter_cache
        ):
            winners = _restore_types(
                pd.read_csv(winners_path, low_memory=False)
            )
            winner_audit = pd.read_csv(audit_path, low_memory=False)
            quarter_summary = json.loads(
                summary_path.read_text(encoding="utf-8")
            )
            reused += 1
        else:
            quarter = load_sec_financial_statement_zip(zip_path)
            winners, winner_audit, audit = build_ttm_duration_winners(
                quarter.submissions,
                quarter.numeric_facts,
                quarter.presentation,
            )
            winners = winners.copy()
            winners["source_zip"] = zip_path.name
            winners.to_csv(winners_path, index=False)
            winner_audit.to_csv(audit_path, index=False)
            quarter_summary = {
                "source_zip": zip_path.name,
                "rows_input": audit.rows_input,
                "rows_mapped": audit.rows_mapped,
                "rows_supported_forms": audit.rows_supported_forms,
                "rows_consolidated": audit.rows_consolidated,
                "rows_statement_matched": audit.rows_statement_matched,
                "rows_period_matched": audit.rows_period_matched,
                "rows_current_period": audit.rows_current_period,
                "rows_numeric_value": audit.rows_numeric_value,
                "rows_output": audit.rows_output,
                "winner_duplicate_groups": audit.winner_duplicate_groups,
                "winner_unresolved_groups": audit.winner_unresolved_groups,
            }
            summary_path.write_text(
                json.dumps(quarter_summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            processed += 1

        if "source_zip" not in winners.columns:
            winners["source_zip"] = zip_path.name
        if not winner_audit.empty and "source_zip" not in winner_audit.columns:
            winner_audit = winner_audit.assign(source_zip=zip_path.name)

        frames.append(winners)
        audit_frames.append(winner_audit)
        quarter_summaries.append(quarter_summary)

        if index == 1 or index % 10 == 0 or index == len(paths):
            print(
                f"Quarter ZIPs {index}/{len(paths)}; "
                f"processed={processed}, reused={reused}",
                flush=True,
            )

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    combined_before = len(combined)
    dedup_columns = [
        column
        for column in (
            "adsh",
            "cik",
            "concept",
            "ddate_date",
            "qtrs",
            "uom",
            "accepted_at",
            "value",
            "source_tag",
        )
        if column in combined.columns
    ]
    if not combined.empty:
        combined = (
            combined.drop_duplicates(subset=dedup_columns, keep="first")
            .sort_values(
                [
                    column
                    for column in (
                        "cik",
                        "accepted_at",
                        "concept",
                        "ddate_date",
                        "qtrs",
                    )
                    if column in combined.columns
                ],
                kind="stable",
            )
            .reset_index(drop=True)
        )

    winner_audit_all = (
        pd.concat(audit_frames, ignore_index=True)
        if audit_frames
        else pd.DataFrame()
    )
    unresolved = (
        winner_audit_all.loc[
            winner_audit_all["resolution"].eq("unresolved_priority_tie")
        ]
        if not winner_audit_all.empty and "resolution" in winner_audit_all
        else pd.DataFrame()
    )

    duration_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output["duration_winners"], index=False)
    winner_audit_all.to_csv(
        output["duration_winner_audit"], index=False
    )

    summary = {
        "schema_version": 1,
        "status": "TTM_DURATION_CACHE_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "zip_dir": str(zip_dir),
        "zip_count": len(paths),
        "quarter_caches_processed": processed,
        "quarter_caches_reused": reused,
        "combined_rows_before_dedup": combined_before,
        "combined_rows_after_dedup": len(combined),
        "combined_duplicate_rows_removed": combined_before - len(combined),
        "unique_ciks": (
            int(combined["cik"].nunique(dropna=True))
            if "cik" in combined
            else 0
        ),
        "winner_audit_rows": len(winner_audit_all),
        "unresolved_winner_groups": len(unresolved),
        "quarter_summaries": quarter_summaries,
        "v1_canonical_artifacts_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }
    output["duration_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "zip_inputs": fingerprint_files(root=root, paths=paths),
        "research_manifest": fingerprint_files(
            root=root, paths=[v2["manifest"]]
        ),
        "code": git_provenance(root),
    }
    output["duration_input_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 TTM DURATION CACHE BUILD COMPLETE")
    print(f"Combined duration winners:  {len(combined):,}")
    print(
        "Duplicates removed:         "
        f"{combined_before - len(combined):,}"
    )
    print(f"Winner audit groups:        {len(winner_audit_all):,}")
    print(f"Unresolved winner groups:   {len(unresolved):,}")
    print(f"Output:                     {output['duration_winners']}")
    print("V1 CANONICAL CACHE AND V1 SCORING WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
