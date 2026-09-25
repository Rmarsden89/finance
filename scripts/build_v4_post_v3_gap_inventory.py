from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import (
    resolve_v2_liabilities_audit_paths,
    resolve_v2_sec_artifact_paths,
    resolve_v2_share_cleanup_paths,
)
from finance.research.v3 import (
    V3_COMBINED_CHALLENGER,
    validate_v3_combined_challenger_freeze,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the post-V3 residual shares/liabilities boundary as the "
            "frozen V4 starting inventory. Read-only with respect to V1/V2/V3."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _positive(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").gt(0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classifications(
    frame: pd.DataFrame,
    *,
    prefix: str,
) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        return pd.DataFrame(columns=["ticker"])

    frame = frame.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    candidates = [
        "classification",
        "recommended_action",
        "status",
        "reason",
        "gap_reason",
        "root_cause",
    ]
    keep = ["ticker"] + [c for c in candidates if c in frame.columns]
    out = frame[keep].drop_duplicates("ticker", keep="first")
    return out.rename(
        columns={c: f"{prefix}_{c}" for c in keep if c != "ticker"}
    )


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
    shares_v2 = resolve_v2_share_cleanup_paths(root, args.as_of)
    liabilities_v2 = resolve_v2_liabilities_audit_paths(root, args.as_of)
    v3_base = root / "reports" / "v3" / "data_sources" / as_of

    liabilities_recovery = (
        v3_base / "v3_liabilities_impact" / "recovery_detail.csv"
    )
    shares_recovery = (
        v3_base / "raw_share_full_residual" / "candidate_detail.csv"
    )
    comparison_summary = (
        v3_base / "v3_current_v2_comparison" / "summary.json"
    )
    determinism = (
        v3_base
        / "v3_current_v2_comparison"
        / "determinism_verification.json"
    )

    required = {
        "V2 current snapshot": v2["current_snapshot"],
        "V2 shares cleanup detail": shares_v2["detail"],
        "V2 liabilities gap detail": liabilities_v2["detail"],
        "V3 liabilities recovery detail": liabilities_recovery,
        "V3 shares recovery detail": shares_recovery,
        "V3 current comparison summary": comparison_summary,
        "V3 determinism verification": determinism,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing post-V3 inventory input(s):\n  " + "\n  ".join(missing)
        )

    det = json.loads(determinism.read_text(encoding="utf-8"))
    if det.get("status") != "V3_DETERMINISM_VERIFIED":
        raise SystemExit("V3 determinism must be verified before freezing V4 baseline")

    snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.upper().str.strip()
    if snapshot["ticker"].duplicated().any():
        raise SystemExit("V2 current snapshot contains duplicate tickers")

    liab = pd.read_csv(liabilities_recovery, low_memory=False)
    liab["ticker"] = liab["ticker"].astype(str).str.upper().str.strip()
    liab["constructed_liabilities"] = pd.to_numeric(
        liab["constructed_liabilities"], errors="coerce"
    )
    liab_recovered = set(
        liab.loc[liab["constructed_liabilities"].gt(0), "ticker"]
    )

    shares = pd.read_csv(shares_recovery, low_memory=False)
    shares["ticker"] = shares["ticker"].astype(str).str.upper().str.strip()
    shares["candidate_value"] = pd.to_numeric(
        shares["candidate_value"], errors="coerce"
    )
    share_recovered = set(
        shares.loc[
            shares["status"].astype(str).eq("candidate")
            & shares["candidate_value"].gt(0),
            "ticker",
        ]
    )

    detail = snapshot[
        [
            c
            for c in (
                "ticker",
                "cik",
                "company_name",
                "shares_outstanding",
                "total_liabilities",
            )
            if c in snapshot.columns
        ]
    ].copy()

    detail["baseline_shares_present"] = _positive(
        snapshot["shares_outstanding"]
    )
    detail["baseline_liabilities_present"] = _positive(
        snapshot["total_liabilities"]
    )
    detail["v3_shares_recovered"] = detail["ticker"].isin(share_recovered)
    detail["v3_liabilities_recovered"] = detail["ticker"].isin(liab_recovered)
    detail["post_v3_shares_present"] = (
        detail["baseline_shares_present"] | detail["v3_shares_recovered"]
    )
    detail["post_v3_liabilities_present"] = (
        detail["baseline_liabilities_present"]
        | detail["v3_liabilities_recovered"]
    )
    detail["post_v3_shares_gap"] = ~detail["post_v3_shares_present"]
    detail["post_v3_liabilities_gap"] = ~detail["post_v3_liabilities_present"]
    detail["post_v3_missing_both"] = (
        detail["post_v3_shares_gap"]
        & detail["post_v3_liabilities_gap"]
    )

    share_class = _classifications(
        pd.read_csv(shares_v2["detail"], low_memory=False),
        prefix="shares",
    )
    liab_class = _classifications(
        pd.read_csv(liabilities_v2["detail"], low_memory=False),
        prefix="liabilities",
    )
    detail = detail.merge(
        share_class, on="ticker", how="left", validate="one_to_one"
    )
    detail = detail.merge(
        liab_class, on="ticker", how="left", validate="one_to_one"
    )

    residual = detail.loc[
        detail["post_v3_shares_gap"] | detail["post_v3_liabilities_gap"]
    ].copy()
    residual["residual_fields"] = ""
    residual.loc[
        residual["post_v3_shares_gap"],
        "residual_fields",
    ] = "shares_outstanding"
    residual.loc[
        residual["post_v3_liabilities_gap"],
        "residual_fields",
    ] = residual.loc[
        residual["post_v3_liabilities_gap"],
        "residual_fields",
    ].replace(
        {
            "": "total_liabilities",
            "shares_outstanding": "shares_outstanding|total_liabilities",
        }
    )
    residual = residual.sort_values(
        ["post_v3_missing_both", "residual_fields", "ticker"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)

    summary = {
        "schema_version": 1,
        "status": "V4_BASELINE_FROZEN",
        "as_of": as_of,
        "source_v3_model_id": V3_COMBINED_CHALLENGER.model_id,
        "source_v3_configuration_hash": (
            V3_COMBINED_CHALLENGER.configuration_hash
        ),
        "universe_rows": int(len(detail)),
        "pre_v3_shares_gaps": int((~detail["baseline_shares_present"]).sum()),
        "v3_shares_recovered": int(detail["v3_shares_recovered"].sum()),
        "post_v3_shares_gaps": int(detail["post_v3_shares_gap"].sum()),
        "pre_v3_liabilities_gaps": int(
            (~detail["baseline_liabilities_present"]).sum()
        ),
        "v3_liabilities_recovered": int(
            detail["v3_liabilities_recovered"].sum()
        ),
        "post_v3_liabilities_gaps": int(
            detail["post_v3_liabilities_gap"].sum()
        ),
        "post_v3_missing_both": int(detail["post_v3_missing_both"].sum()),
        "unique_residual_tickers": int(residual["ticker"].nunique()),
        "source_artifact_sha256": {
            name: _sha256(path)
            for name, path in required.items()
        },
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    grouped = pd.DataFrame(
        [
            {
                "field": "shares_outstanding",
                "pre_v3_gap_rows": summary["pre_v3_shares_gaps"],
                "v3_recovered_rows": summary["v3_shares_recovered"],
                "post_v3_gap_rows": summary["post_v3_shares_gaps"],
            },
            {
                "field": "total_liabilities",
                "pre_v3_gap_rows": summary["pre_v3_liabilities_gaps"],
                "v3_recovered_rows": summary["v3_liabilities_recovered"],
                "post_v3_gap_rows": summary["post_v3_liabilities_gaps"],
            },
        ]
    )

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "post_v3_gap_inventory"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    detail_path = output_dir / "post_v3_residual_gap_inventory.csv"
    grouped_path = output_dir / "post_v3_gap_summary.csv"
    summary_path = output_dir / "summary.json"

    residual.to_csv(detail_path, index=False)
    grouped.to_csv(grouped_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 POST-V3 RESIDUAL GAP BASELINE")
    print(f"As of:                       {as_of}")
    print(f"Universe rows:               {summary['universe_rows']}")
    print(
        f"Shares gaps:                 "
        f"{summary['pre_v3_shares_gaps']} -> "
        f"{summary['post_v3_shares_gaps']} "
        f"(-{summary['v3_shares_recovered']})"
    )
    print(
        f"Liabilities gaps:            "
        f"{summary['pre_v3_liabilities_gaps']} -> "
        f"{summary['post_v3_liabilities_gaps']} "
        f"(-{summary['v3_liabilities_recovered']})"
    )
    print(
        f"Residual tickers:            "
        f"{summary['unique_residual_tickers']}"
    )
    print(
        f"Missing both fields:         "
        f"{summary['post_v3_missing_both']}"
    )
    print(f"Inventory:                   {detail_path}")
    print(f"Summary by field:            {grouped_path}")
    print(f"Frozen baseline summary:     {summary_path}")
    print("V1/V2/V3 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
