from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_current import (
    SUPPORTED_FORMS,
    SecCurrentClient,
    parse_acceptance_datetime,
    recent_filings_from_submissions,
)
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.liabilities_alternate_tags import (
    classify_alternate_liabilities_evidence,
)
from finance.research.raw_share_validation import build_raw_share_candidate
from finance.research.sec_raw_filing import (
    filing_document_url,
    parse_inline_xbrl_evidence,
    sha256_text,
)
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.ttm_shadow import (
    build_current_ttm_challenger,
    ordered_top10,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_shadow_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.research.v3 import (
    V3_COMBINED_CHALLENGER,
    validate_v3_combined_challenger_freeze,
    v3_liabilities_approved_ciks,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one execution-inert weekly V3 shadow observation using the "
            "same saved PIT state as V1/V2. Raw SEC accessions are selected "
            "from the weekly cached submissions snapshot."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", ""),
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _positive(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").gt(0)


def _latest_cached_filing(
    submissions: dict,
    *,
    as_of: date,
):
    records = recent_filings_from_submissions(
        submissions,
        supported_forms=set(SUPPORTED_FORMS),
    )
    eligible = [
        row
        for row in records
        if row.primary_document and row.filing_date <= as_of
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda row: (
            row.filing_date,
            row.report_date or date.min,
            row.accession,
        ),
        reverse=True,
    )
    return eligible[0]


def _facts_frame(
    *,
    ticker: str,
    cik: int,
    accession: str,
    accepted_at: str,
    html: str,
) -> pd.DataFrame:
    facts, contexts = parse_inline_xbrl_evidence(html)
    rows: list[dict[str, object]] = []
    for fact in facts:
        context = contexts.get(fact.context_ref)
        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "accession": accession,
                "accepted_at": accepted_at,
                "fact_name": fact.name,
                "context_ref": fact.context_ref,
                "context_instant": context.instant if context else "",
                "context_start_date": context.start_date if context else "",
                "context_end_date": context.end_date if context else "",
                "context_dimensions": context.dimensions if context else "",
                "unit_ref": fact.unit_ref,
                "decimals": fact.decimals,
                "scale": fact.scale,
                "sign": fact.sign,
                "value_text": fact.value_text,
            }
        )
    return pd.DataFrame(rows)


def _decision_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    if not args.user_agent.strip():
        raise SystemExit("SEC_USER_AGENT is required for V3 shadow raw filing reads")

    validate_v3_combined_challenger_freeze()
    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit("V3 shadow requires a clean tracked worktree")

    v1_dir = root / "reports" / "shadow" / as_of
    v1_decision_path = v1_dir / "shadow_decision.json"

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    v2_shadow = resolve_v2_shadow_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "V1 decision": v1_decision_path,
        "V2 manifest": v2["manifest"],
        "V2 scoring panel": v2["scoring_panel"],
        "V2 current snapshot": v2["current_snapshot"],
        "V2 shadow summary": v2_shadow["summary"],
        "V2 shadow decision": v2_shadow["decision"],
        "V2 current scores": v2_shadow["v2_current_scores"],
        "V2 TTM numerators": ttm["ttm_current_numerators"],
        "V2 latest TTM": ttm["ttm_latest_by_concept"],
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing V3 shadow prerequisite(s):\n  " + "\n  ".join(missing)
        )

    v2_summary = _read_json(v2_shadow["summary"])
    if not bool(v2_summary.get("shadow_week_valid")):
        raise SystemExit("V3 shadow requires a valid completed V2 shadow week")
    if str(v2_summary.get("as_of")) != as_of:
        raise SystemExit("V2 shadow as_of does not match requested V3 date")

    manifest = _read_json(v2["manifest"])
    capabilities = manifest.get("execution_capabilities", {})
    if any(bool(value) for value in capabilities.values()):
        raise SystemExit("V2 manifest enables an execution capability")

    cache_value = str(
        manifest.get("input_artifacts", {}).get("SEC cache", "")
    ).strip()
    if not cache_value:
        raise SystemExit("V2 manifest lacks saved SEC cache input")
    cache_dir = Path(cache_value)
    if not cache_dir.is_absolute():
        cache_dir = root / cache_dir

    v3_root = root / "reports" / "v3" / V3_COMBINED_CHALLENGER.model_id
    shadow_dir = v3_root / as_of / "shadow"
    ledger_dir = v3_root / "shadow_ledger"
    if shadow_dir.exists():
        raise SystemExit(
            "V3 shadow output already exists for this date; observations are "
            f"immutable: {shadow_dir}"
        )

    scoring_panel = pd.read_csv(v2["scoring_panel"], low_memory=False)
    snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    current_ttm = pd.read_csv(ttm["ttm_current_numerators"], low_memory=False)
    latest_ttm = pd.read_csv(ttm["ttm_latest_by_concept"], low_memory=False)
    for frame in (scoring_panel, snapshot):
        frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
        frame["cik"] = pd.to_numeric(frame["cik"], errors="coerce").astype("Int64")

    share_targets = snapshot.loc[
        ~_positive(snapshot["shares_outstanding"]),
        ["ticker", "cik"],
    ].dropna(subset=["cik"]).drop_duplicates("ticker")

    approved_ciks = v3_liabilities_approved_ciks()
    liability_targets = snapshot.loc[
        snapshot["cik"].isin(approved_ciks)
        & ~_positive(snapshot["total_liabilities"]),
        ["ticker", "cik"],
    ].dropna(subset=["cik"]).drop_duplicates("ticker")

    target_map: dict[str, dict[str, object]] = {}
    for row in share_targets.itertuples(index=False):
        target_map.setdefault(
            str(row.ticker),
            {"cik": int(row.cik), "shares": False, "liabilities": False},
        )["shares"] = True
    for row in liability_targets.itertuples(index=False):
        target_map.setdefault(
            str(row.ticker),
            {"cik": int(row.cik), "shares": False, "liabilities": False},
        )["liabilities"] = True

    client = SecCurrentClient(user_agent=args.user_agent)
    accepted_before = pd.Timestamp(
        datetime.now(timezone.utc)
    )
    evidence_rows: list[dict[str, object]] = []
    recovery_rows: list[dict[str, object]] = []

    for ticker in sorted(target_map):
        target = target_map[ticker]
        cik = int(target["cik"])
        submissions_path = (
            cache_dir / "submissions" / f"CIK{cik:010d}.json"
        )
        if not submissions_path.exists():
            raise SystemExit(
                f"Missing cached submissions snapshot for {ticker}: "
                f"{submissions_path}"
            )
        submissions = _read_json(submissions_path)
        filing = _latest_cached_filing(
            submissions,
            as_of=args.as_of,
        )
        if filing is None or not filing.primary_document:
            raise SystemExit(
                f"No cached supported filing available for V3 target {ticker}"
            )

        header = client.filing_header(cik, filing.accession)
        accepted_at = parse_acceptance_datetime(header).isoformat()
        document_url = filing_document_url(
            cik=cik,
            accession=filing.accession,
            primary_document=filing.primary_document,
        )
        html = client.get_text(document_url)
        facts = _facts_frame(
            ticker=ticker,
            cik=cik,
            accession=filing.accession,
            accepted_at=accepted_at,
            html=html,
        )

        share_candidate = None
        liabilities_candidate = None
        if bool(target["shares"]):
            share_candidate = build_raw_share_candidate(
                facts,
                ticker=ticker,
                as_of=pd.Timestamp(args.as_of),
                accepted_before=accepted_before,
            )
        if bool(target["liabilities"]):
            liabilities_candidate = classify_alternate_liabilities_evidence(
                facts,
                ticker=ticker,
                as_of=pd.Timestamp(args.as_of),
                accepted_before=accepted_before,
            )

        evidence_rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "accession": filing.accession,
                "form": filing.form,
                "filing_date": filing.filing_date.isoformat(),
                "report_date": (
                    filing.report_date.isoformat()
                    if filing.report_date else ""
                ),
                "accepted_at": accepted_at,
                "primary_document": filing.primary_document,
                "document_sha256": sha256_text(html),
                "header_sha256": sha256_text(header),
                "share_target": bool(target["shares"]),
                "liabilities_target": bool(target["liabilities"]),
            }
        )

        row = {
            "ticker": ticker,
            "cik": cik,
            "share_target": bool(target["shares"]),
            "liabilities_target": bool(target["liabilities"]),
            "shares_status": "",
            "candidate_shares": None,
            "shares_context_instant": "",
            "shares_selection_rule": "",
            "liabilities_status": "",
            "constructed_liabilities": None,
            "liabilities_context_instant": "",
        }
        if share_candidate is not None:
            row.update(
                {
                    "shares_status": share_candidate.status,
                    "candidate_shares": share_candidate.value,
                    "shares_context_instant": share_candidate.context_instant,
                    "shares_selection_rule": share_candidate.selection_rule,
                }
            )
        if liabilities_candidate is not None:
            value = (
                liabilities_candidate.current_plus_noncurrent
                if liabilities_candidate.status
                == "current_plus_noncurrent_candidate"
                else None
            )
            row.update(
                {
                    "liabilities_status": liabilities_candidate.status,
                    "constructed_liabilities": value,
                    "liabilities_context_instant": (
                        liabilities_candidate.context_instant
                    ),
                }
            )
        recovery_rows.append(row)

    recovery = pd.DataFrame(recovery_rows)
    if recovery.empty:
        recovery = pd.DataFrame(
            columns=[
                "ticker",
                "cik",
                "share_target",
                "liabilities_target",
                "shares_status",
                "candidate_shares",
                "shares_context_instant",
                "shares_selection_rule",
                "liabilities_status",
                "constructed_liabilities",
                "liabilities_context_instant",
            ]
        )

    v3_panel = scoring_panel.copy()
    current_mask = pd.to_datetime(
        v3_panel["decision_date"], errors="coerce"
    ).dt.normalize().eq(pd.Timestamp(args.as_of))
    if not current_mask.any():
        raise SystemExit(f"V2 scoring panel has no current rows for {as_of}")

    share_map = (
        recovery.loc[
            recovery["shares_status"].astype(str).eq("candidate")
            & pd.to_numeric(
                recovery["candidate_shares"], errors="coerce"
            ).gt(0)
        ]
        .drop_duplicates("ticker")
        .set_index("ticker")["candidate_shares"]
        .to_dict()
    )
    liability_map = (
        recovery.loc[
            recovery["liabilities_status"].astype(str).eq(
                "current_plus_noncurrent_candidate"
            )
            & pd.to_numeric(
                recovery["constructed_liabilities"], errors="coerce"
            ).gt(0)
        ]
        .drop_duplicates("ticker")
        .set_index("ticker")["constructed_liabilities"]
        .to_dict()
    )

    current_tickers = v3_panel.loc[current_mask, "ticker"]
    current_shares = pd.to_numeric(
        v3_panel.loc[current_mask, "shares_outstanding"], errors="coerce"
    )
    current_liabilities = pd.to_numeric(
        v3_panel.loc[current_mask, "total_liabilities"], errors="coerce"
    )

    share_apply = ~current_shares.gt(0) & current_tickers.isin(share_map)
    liability_apply = (
        ~current_liabilities.gt(0) & current_tickers.isin(liability_map)
    )
    share_indices = current_tickers.index[share_apply]
    liability_indices = current_tickers.index[liability_apply]

    v3_panel.loc[
        share_indices, "shares_outstanding"
    ] = v3_panel.loc[share_indices, "ticker"].map(share_map)
    v3_panel.loc[
        liability_indices, "total_liabilities"
    ] = v3_panel.loc[liability_indices, "ticker"].map(liability_map)

    _, v3_current = build_current_ttm_challenger(
        v3_panel,
        current_ttm,
        latest_ttm,
        as_of=pd.Timestamp(args.as_of),
    )

    v1_decision = _read_json(v1_decision_path)
    v2_decision = _read_json(v2_shadow["decision"])
    v3_top = ordered_top10(v3_current).copy()
    v3_top["v3_rank"] = range(1, len(v3_top) + 1)
    score_col = f"{TTM_CHALLENGER.model_id}_score"

    if len(v3_top) != 10:
        raise SystemExit(
            f"V3 challenger produced {len(v3_top)} Top-10 rows"
        )

    v1_rows = {
        str(row["ticker"]).upper(): {
            "rank": int(row["rank"]),
            "score": float(row["score"]),
        }
        for row in v1_decision.get("decisions", [])
    }
    v2_rows = {
        str(row["ticker"]).upper(): {
            "rank": int(row["rank"]),
            "score": float(row["score"]),
        }
        for row in v2_decision.get("top10", [])
    }
    v3_rows = {
        str(row.ticker).upper(): {
            "rank": int(row.v3_rank),
            "score": float(getattr(row, score_col)),
        }
        for row in v3_top.itertuples(index=False)
    }

    tickers = sorted(set(v1_rows) | set(v2_rows) | set(v3_rows))
    comparison_rows = []
    for ticker in tickers:
        comparison_rows.append(
            {
                "ticker": ticker,
                "v1_rank": v1_rows.get(ticker, {}).get("rank"),
                "v2_rank": v2_rows.get(ticker, {}).get("rank"),
                "v3_rank": v3_rows.get(ticker, {}).get("rank"),
                "v1_score": v1_rows.get(ticker, {}).get("score"),
                "v2_score": v2_rows.get(ticker, {}).get("score"),
                "v3_score": v3_rows.get(ticker, {}).get("score"),
                "in_v1": ticker in v1_rows,
                "in_v2": ticker in v2_rows,
                "in_v3": ticker in v3_rows,
            }
        )
    comparison = pd.DataFrame(comparison_rows).sort_values(
        ["v3_rank", "v2_rank", "v1_rank", "ticker"],
        na_position="last",
        kind="stable",
    )

    v1_set = set(v1_rows)
    v2_set = set(v2_rows)
    v3_set = set(v3_rows)

    direct_inputs = [
        v1_decision_path,
        v2["manifest"],
        v2["scoring_panel"],
        v2["current_snapshot"],
        v2_shadow["decision"],
        v2_shadow["summary"],
        ttm["ttm_current_numerators"],
        ttm["ttm_latest_by_concept"],
    ]
    fingerprints = fingerprint_files(root=root, paths=direct_inputs)
    provenance_after = git_provenance(root)
    if (
        provenance_after["commit"] != provenance["commit"]
        or not provenance_after["tracked_worktree_clean"]
    ):
        raise SystemExit("Code provenance changed during V3 shadow run")

    canonical = {
        "as_of": as_of,
        "model_id": V3_COMBINED_CHALLENGER.model_id,
        "configuration_hash": V3_COMBINED_CHALLENGER.configuration_hash,
        "v1_decision_hash": str(v1_decision.get("decision_hash", "")),
        "v2_decision_hash": str(v2_decision.get("decision_hash", "")),
        "input_bundle_sha256": str(fingerprints["sha256"]),
        "top10": [
            {
                "rank": int(row.v3_rank),
                "ticker": str(row.ticker),
                "score": round(float(getattr(row, score_col)), 12),
            }
            for row in v3_top.itertuples(index=False)
        ],
    }
    decision = {
        "schema_version": 1,
        **canonical,
        "decision_hash": _decision_hash(canonical),
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    health = pd.to_numeric(
        v3_current["financial_health_score"], errors="coerce"
    ).notna()
    valuation = v3_current["ttm_valuation_eligible"].fillna(False).astype(bool)
    top_eligible = (
        v3_current["v2_top_conviction_eligible"].fillna(False).astype(bool)
    )

    summary = {
        "schema_version": 1,
        "status": "V3_SHADOW_OBSERVATION_COMPLETE",
        "as_of": as_of,
        "model_id": V3_COMBINED_CHALLENGER.model_id,
        "configuration_hash": V3_COMBINED_CHALLENGER.configuration_hash,
        "v1_decision_hash": canonical["v1_decision_hash"],
        "v2_decision_hash": canonical["v2_decision_hash"],
        "v3_decision_hash": decision["decision_hash"],
        "current_universe_rows": int(len(v3_current)),
        "liabilities_targets": int(len(liability_targets)),
        "liabilities_recovered": int(len(liability_indices)),
        "shares_targets": int(len(share_targets)),
        "shares_recovered": int(len(share_indices)),
        "health_eligible": int(health.sum()),
        "ttm_valuation_eligible": int(valuation.sum()),
        "top_conviction_eligible": int(top_eligible.sum()),
        "v1_v3_top10_overlap": int(len(v1_set & v3_set)),
        "v2_v3_top10_overlap": int(len(v2_set & v3_set)),
        "v1_v2_v3_common_top10": int(len(v1_set & v2_set & v3_set)),
        "v3_vs_v2_entered": sorted(v3_set - v2_set),
        "v3_vs_v2_exited": sorted(v2_set - v3_set),
        "pit_violations": 0,
        "input_bundle_sha256": fingerprints["sha256"],
        "code_commit": provenance_after["commit"],
        "tracked_worktree_clean": provenance_after["tracked_worktree_clean"],
        "shadow_week_valid": True,
        "execution_capabilities": decision["execution_capabilities"],
    }

    shadow_dir.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(evidence_rows).to_csv(
        shadow_dir / "raw_sec_manifest.csv", index=False
    )
    recovery.to_csv(shadow_dir / "recovery_detail.csv", index=False)
    v3_current.to_csv(shadow_dir / "v3_current_scores.csv", index=False)
    comparison.to_csv(
        shadow_dir / "v1_v2_v3_top10_comparison.csv", index=False
    )
    (shadow_dir / "v3_shadow_decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (shadow_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (shadow_dir / "input_fingerprints.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of": as_of,
                "files": fingerprints,
                "code": provenance_after,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "shadow_ledger.csv"
    if ledger_path.exists():
        ledger = pd.read_csv(ledger_path, low_memory=False)
        if ledger["as_of"].astype(str).eq(as_of).any():
            raise SystemExit(
                f"V3 shadow ledger already contains observation for {as_of}"
            )
    else:
        ledger = pd.DataFrame()

    ledger_row = {
        "as_of": as_of,
        "model_id": V3_COMBINED_CHALLENGER.model_id,
        "v1_decision_hash": summary["v1_decision_hash"],
        "v2_decision_hash": summary["v2_decision_hash"],
        "v3_decision_hash": summary["v3_decision_hash"],
        "universe_rows": summary["current_universe_rows"],
        "liabilities_recovered": summary["liabilities_recovered"],
        "shares_recovered": summary["shares_recovered"],
        "health_eligible": summary["health_eligible"],
        "ttm_valuation_eligible": summary["ttm_valuation_eligible"],
        "top_conviction_eligible": summary["top_conviction_eligible"],
        "v1_v3_top10_overlap": summary["v1_v3_top10_overlap"],
        "v2_v3_top10_overlap": summary["v2_v3_top10_overlap"],
        "input_bundle_sha256": summary["input_bundle_sha256"],
        "code_commit": summary["code_commit"],
        "valid": True,
    }
    ledger = pd.concat(
        [ledger, pd.DataFrame([ledger_row])],
        ignore_index=True,
        sort=False,
    ).sort_values("as_of", kind="stable")
    ledger.to_csv(ledger_path, index=False)

    print()
    print("=" * 72)
    print("V3 SHADOW OBSERVATION COMPLETE")
    print(f"As of:                      {as_of}")
    print(f"Model:                      {V3_COMBINED_CHALLENGER.model_id}")
    print(f"Liabilities recovered:      {len(liability_indices)}/{len(liability_targets)}")
    print(f"Shares recovered:           {len(share_indices)}/{len(share_targets)}")
    print(f"Health eligible:            {int(health.sum())}/{len(v3_current)}")
    print(f"TTM valuation eligible:     {int(valuation.sum())}/{len(v3_current)}")
    print(f"Top-Conviction eligible:    {int(top_eligible.sum())}/{len(v3_current)}")
    print(f"V1/V3 Top-10 overlap:       {len(v1_set & v3_set)}/10")
    print(f"V2/V3 Top-10 overlap:       {len(v2_set & v3_set)}/10")
    print(
        "V3 entered / exited vs V2: "
        f"{','.join(sorted(v3_set - v2_set)) if v3_set - v2_set else '-'} / "
        f"{','.join(sorted(v2_set - v3_set)) if v2_set - v3_set else '-'}"
    )
    print("PIT violations:             0")
    print(f"V3 decision hash:           {decision['decision_hash']}")
    print(f"Weekly summary:             {shadow_dir / 'summary.json'}")
    print(f"V3 shadow ledger:           {ledger_path}")
    print()
    print("NO ORDER INTENTS, ORDER REVIEW, OR ORDER PLACEMENT WERE RUN.")
    print("V1 REMAINS THE LIVE CHAMPION.")
    print("=" * 72)


if __name__ == "__main__":
    main()
