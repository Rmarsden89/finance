from __future__ import annotations

import hashlib
import json

import pandas as pd

from finance.research.ttm_challenger import add_long_growth_v2_ttm_scores
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.ttm_valuation_family import (
    add_ttm_valuation_factors,
    add_ttm_valuation_family_score,
    normalize_ttm_valuation_factors,
)
from finance.research.v2_impact import score_long_growth_panel


def build_current_ttm_challenger(
    scoring_panel: pd.DataFrame,
    current_ttm: pd.DataFrame,
    latest_ttm: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score the frozen current V2 TTM challenger with no execution state."""

    scored = score_long_growth_panel(scoring_panel)
    decision_dates = pd.to_datetime(
        scored["decision_date"], errors="coerce"
    ).dt.normalize()
    current = scored.loc[
        decision_dates.eq(as_of.normalize())
    ].copy()
    if current.empty:
        raise ValueError(
            f"V2 scoring panel has no rows for {as_of.date().isoformat()}"
        )
    if current["ticker"].duplicated().any():
        raise ValueError("V2 current scoring rows contain duplicate tickers")

    ttm_frame = add_ttm_valuation_factors(
        current,
        current_ttm,
        latest_ttm,
    )
    ttm_frame = normalize_ttm_valuation_factors(ttm_frame)
    ttm_frame = add_ttm_valuation_family_score(ttm_frame)
    challenger = add_long_growth_v2_ttm_scores(ttm_frame)
    return scored, challenger


def ordered_top10(frame: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic frozen-challenger Top 10."""

    score_column = f"{TTM_CHALLENGER.model_id}_score"
    eligible = frame.loc[
        frame["v2_top_conviction_eligible"].fillna(False).astype(bool)
        & pd.to_numeric(frame[score_column], errors="coerce").notna()
    ].copy()
    eligible = eligible.sort_values(
        [score_column, "ticker"],
        ascending=[False, True],
        kind="stable",
    )
    top = eligible.head(TTM_CHALLENGER.top_n).copy()
    top["v2_rank"] = range(1, len(top) + 1)
    return top


def build_top10_comparison(
    *,
    v1_decision: dict,
    v2_current: pd.DataFrame,
) -> pd.DataFrame:
    """Compare the saved live V1 model Top 10 with the V2 shadow Top 10."""

    v1_rows = sorted(
        v1_decision.get("decisions", []),
        key=lambda row: int(row["rank"]),
    )
    v1 = pd.DataFrame([
        {
            "ticker": str(row["ticker"]).upper(),
            "v1_rank": int(row["rank"]),
            "v1_score": float(row["score"]),
        }
        for row in v1_rows
    ])
    if len(v1) != TTM_CHALLENGER.top_n:
        raise ValueError(
            f"Saved V1 decision must contain exactly {TTM_CHALLENGER.top_n} rows"
        )
    if v1["ticker"].duplicated().any():
        raise ValueError("Saved V1 decision contains duplicate tickers")

    v2_top = ordered_top10(v2_current)
    if len(v2_top) != TTM_CHALLENGER.top_n:
        raise ValueError(
            f"V2 challenger produced {len(v2_top)} Top-10 rows"
        )
    score_column = f"{TTM_CHALLENGER.model_id}_score"
    v2 = v2_top[["ticker", "v2_rank", score_column]].rename(
        columns={score_column: "v2_score"}
    )

    comparison = v1.merge(
        v2,
        on="ticker",
        how="outer",
        validate="one_to_one",
    )
    comparison["in_v1_top10"] = comparison["v1_rank"].notna()
    comparison["in_v2_top10"] = comparison["v2_rank"].notna()
    comparison["in_both"] = (
        comparison["in_v1_top10"] & comparison["in_v2_top10"]
    )
    comparison["change"] = "overlap"
    comparison.loc[
        comparison["in_v2_top10"] & ~comparison["in_v1_top10"],
        "change",
    ] = "entered_v2"
    comparison.loc[
        comparison["in_v1_top10"] & ~comparison["in_v2_top10"],
        "change",
    ] = "exited_v2"
    comparison["rank_change_v2_minus_v1"] = (
        pd.to_numeric(comparison["v2_rank"], errors="coerce")
        - pd.to_numeric(comparison["v1_rank"], errors="coerce")
    )
    return comparison.sort_values(
        ["v2_rank", "v1_rank", "ticker"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)


def build_v2_decision_payload(
    *,
    as_of: str,
    v1_decision_hash: str,
    v2_current: pd.DataFrame,
    input_bundle_sha256: str,
) -> dict[str, object]:
    top = ordered_top10(v2_current)
    score_column = f"{TTM_CHALLENGER.model_id}_score"
    rows = [
        {
            "rank": int(row.v2_rank),
            "ticker": str(row.ticker),
            "score": round(float(getattr(row, score_column)), 12),
        }
        for row in top.itertuples(index=False)
    ]
    canonical = {
        "as_of": as_of,
        "model_id": TTM_CHALLENGER.model_id,
        "v1_decision_hash": v1_decision_hash,
        "input_bundle_sha256": input_bundle_sha256,
        "top10": rows,
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        **canonical,
        "decision_hash": digest,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }


def append_shadow_ledger(
    ledger: pd.DataFrame,
    row: dict[str, object],
) -> pd.DataFrame:
    """Append one successful unique weekly observation, never overwrite."""

    as_of = str(row["as_of"])
    if not ledger.empty and "as_of" in ledger.columns:
        existing = ledger["as_of"].astype(str).eq(as_of)
        if existing.any():
            raise ValueError(
                f"Shadow ledger already contains observation for {as_of}"
            )

    updated = pd.concat(
        [ledger, pd.DataFrame([row])],
        ignore_index=True,
        sort=False,
    )
    return updated.sort_values("as_of", kind="stable").reset_index(drop=True)
