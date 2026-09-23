from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


TTM_VALUATION_WEIGHTS = {
    "earnings_yield_ttm": 0.30,
    "sales_yield_ttm": 0.20,
    "free_cash_flow_yield_ttm": 0.30,
    "book_to_market": 0.20,
}
ANNUAL_VALUATION_WEIGHTS = {
    "earnings_yield_annual": 0.30,
    "sales_yield_annual": 0.20,
    "free_cash_flow_yield_annual": 0.30,
    "book_to_market": 0.20,
}


@dataclass(frozen=True)
class TtmValuationConfig:
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99
    min_cross_section: int = 20
    minimum_factors: int = 2
    market_cap_scale_floor: float = 0.001
    market_cap_scale_ceiling: float = 1000.0


DEFAULT_TTM_VALUATION = TtmValuationConfig()


def add_ttm_valuation_factors(
    current_snapshot: pd.DataFrame,
    current_ttm: pd.DataFrame,
    latest_ttm: pd.DataFrame,
    *,
    config: TtmValuationConfig = DEFAULT_TTM_VALUATION,
) -> pd.DataFrame:
    """Build current-state V2 TTM valuation factors without touching V1."""

    base = current_snapshot.copy()
    if base["ticker"].duplicated().any():
        raise ValueError("Current snapshot must contain one row per ticker")

    ttm = current_ttm.copy()
    if ttm["ticker"].duplicated().any():
        raise ValueError("Current TTM numerators must contain one row per ticker")

    result = base.merge(
        ttm.drop(columns=["company_name"], errors="ignore"),
        on=["ticker", "cik"],
        how="left",
        validate="one_to_one",
    )

    latest = latest_ttm.copy()
    latest["available_at"] = pd.to_datetime(
        latest["available_at"], errors="coerce"
    )
    latest["ttm_end_date"] = pd.to_datetime(
        latest["ttm_end_date"], errors="coerce"
    )
    simple = latest.loc[
        latest["concept"].isin({"revenue", "net_income"}),
        ["cik", "concept", "available_at", "ttm_end_date"],
    ].copy()
    if not simple.empty:
        avail = simple.pivot(
            index="cik", columns="concept", values="available_at"
        ).reset_index()
        ends = simple.pivot(
            index="cik", columns="concept", values="ttm_end_date"
        ).reset_index()
        avail = avail.rename(
            columns={
                "revenue": "ttm_revenue_available_at",
                "net_income": "ttm_net_income_available_at",
            }
        )
        ends = ends.rename(
            columns={
                "revenue": "ttm_revenue_end_date",
                "net_income": "ttm_net_income_end_date",
            }
        )
        result = result.merge(avail, on="cik", how="left", validate="many_to_one")
        result = result.merge(ends, on="cik", how="left", validate="many_to_one")

    close = _numeric(result, "close")
    shares = _numeric(result, "shares_outstanding")
    market_cap = pd.Series(np.nan, index=result.index, dtype="float64")
    valid_cap = close.notna() & shares.notna() & close.gt(0) & shares.gt(0)
    market_cap.loc[valid_cap] = close.loc[valid_cap] * shares.loc[valid_cap]
    market_cap.loc[~np.isfinite(market_cap)] = np.nan
    result["market_cap_ttm"] = market_cap

    result["earnings_yield_ttm"] = _yield_ratio(
        _numeric(result, "ttm_net_income"), market_cap
    )
    result["sales_yield_ttm"] = _yield_ratio(
        _numeric(result, "ttm_revenue"), market_cap
    )
    result["free_cash_flow_yield_ttm"] = _yield_ratio(
        _numeric(result, "ttm_free_cash_flow"), market_cap
    )

    equity = _numeric(result, "shareholders_equity")
    book = pd.Series(np.nan, index=result.index, dtype="float64")
    valid_book = equity.notna() & equity.gt(0) & market_cap.notna()
    book.loc[valid_book] = equity.loc[valid_book] / market_cap.loc[valid_book]
    book.loc[~np.isfinite(book)] = np.nan
    result["book_to_market_ttm_family"] = book

    decision = pd.to_datetime(
        result.get("as_of", result.get("decision_date")),
        errors="coerce",
        utc=True,
    ).dt.tz_convert(None)

    total_assets = _numeric(result, "total_assets")
    scale_revenue = _numeric(result, "ttm_revenue")
    cap_to_assets = market_cap / total_assets.where(total_assets.gt(0))
    cap_to_sales = market_cap / scale_revenue.where(scale_revenue.gt(0))
    scale_bad = (
        cap_to_assets.notna()
        & (
            cap_to_assets.lt(config.market_cap_scale_floor)
            | cap_to_assets.gt(config.market_cap_scale_ceiling)
        )
    ) | (
        cap_to_sales.notna()
        & (
            cap_to_sales.lt(config.market_cap_scale_floor)
            | cap_to_sales.gt(config.market_cap_scale_ceiling)
        )
    )

    specs = {
        "earnings_yield_ttm": (
            "ttm_net_income",
            "ttm_net_income_available_at",
            "ttm_net_income_end_date",
        ),
        "sales_yield_ttm": (
            "ttm_revenue",
            "ttm_revenue_available_at",
            "ttm_revenue_end_date",
        ),
        "free_cash_flow_yield_ttm": (
            "ttm_free_cash_flow",
            "ttm_cash_flow_available_at",
            "ttm_cash_flow_end_date",
        ),
    }
    for factor, (numerator, available_col, end_col) in specs.items():
        raw = _numeric(result, factor)
        valid = raw.notna()
        reason = pd.Series("", index=result.index, dtype="object")

        _invalidate(valid, reason, market_cap.notna() & market_cap.le(0), "nonpositive_market_cap")
        _invalidate(valid, reason, scale_bad, "market_cap_scale_mismatch")

        if factor == "sales_yield_ttm":
            revenue = _numeric(result, numerator)
            _invalidate(valid, reason, revenue.notna() & revenue.le(0), "nonpositive_ttm_revenue")

        available = pd.to_datetime(
            result.get(available_col), errors="coerce", utc=True
        ).dt.tz_convert(None)
        end_date = pd.to_datetime(result.get(end_col), errors="coerce")
        numerator_present = _numeric(result, numerator).notna()

        _invalidate(
            valid,
            reason,
            numerator_present & available.isna(),
            "missing_ttm_availability",
        )
        _invalidate(
            valid,
            reason,
            available.notna() & decision.notna() & available.gt(decision),
            "ttm_available_after_decision",
        )
        decision_day = decision.dt.normalize()
        _invalidate(
            valid,
            reason,
            end_date.notna() & decision_day.notna() & end_date.gt(decision_day),
            "ttm_period_end_after_decision",
        )

        result[f"{factor}_valid"] = valid
        result[f"{factor}_invalid_reason"] = reason
        result[f"{factor}_validated"] = raw.where(valid)

    book_raw = _numeric(result, "book_to_market_ttm_family")
    book_valid = book_raw.notna()
    book_reason = pd.Series("", index=result.index, dtype="object")
    _invalidate(book_valid, book_reason, scale_bad, "market_cap_scale_mismatch")
    _invalidate(
        book_valid,
        book_reason,
        equity.notna() & equity.le(0),
        "nonpositive_equity",
    )
    result["book_to_market_ttm_family_valid"] = book_valid
    result["book_to_market_ttm_family_invalid_reason"] = book_reason
    result["book_to_market_ttm_family_validated"] = book_raw.where(book_valid)

    return result


def normalize_ttm_valuation_factors(
    frame: pd.DataFrame,
    *,
    config: TtmValuationConfig = DEFAULT_TTM_VALUATION,
) -> pd.DataFrame:
    """Apply the frozen V1 cross-sectional normalization convention."""

    result = frame.copy()
    mapping = {
        "earnings_yield_ttm": "earnings_yield_ttm_validated",
        "sales_yield_ttm": "sales_yield_ttm_validated",
        "free_cash_flow_yield_ttm": "free_cash_flow_yield_ttm_validated",
        "book_to_market": "book_to_market_ttm_family_validated",
    }
    for factor, validated_col in mapping.items():
        values = _numeric(result, validated_col)
        finite = values[np.isfinite(values)]
        winsorized = pd.Series(np.nan, index=result.index, dtype="float64")
        percentile = pd.Series(np.nan, index=result.index, dtype="float64")
        flag = pd.Series(False, index=result.index, dtype="bool")

        if len(finite) >= config.min_cross_section:
            lower = finite.quantile(config.lower_quantile)
            upper = finite.quantile(config.upper_quantile)
            clipped = values.clip(lower=lower, upper=upper)
            winsorized = clipped
            flag = values.notna() & ((values < lower) | (values > upper))
            percentile = clipped.rank(method="average", pct=True, na_option="keep")

        score_name = factor
        result[f"{score_name}_winsorized"] = winsorized
        result[f"{score_name}_winsorized_flag"] = flag
        result[f"{score_name}_percentile"] = percentile
        result[f"{score_name}_score"] = percentile * 100.0

    return result


def add_ttm_valuation_family_score(
    frame: pd.DataFrame,
    *,
    config: TtmValuationConfig = DEFAULT_TTM_VALUATION,
) -> pd.DataFrame:
    """Score the TTM valuation family using V1 valuation weights."""

    result = frame.copy()
    score_columns = {
        "earnings_yield_ttm": "earnings_yield_ttm_score",
        "sales_yield_ttm": "sales_yield_ttm_score",
        "free_cash_flow_yield_ttm": "free_cash_flow_yield_ttm_score",
        "book_to_market": "book_to_market_score",
    }

    # Keep book/market isolated from the V1 column namespace internally while
    # preserving the same economics and weight.
    if "book_to_market_score" not in result.columns:
        result["book_to_market_score"] = result[
            "book_to_market_score"
        ] if "book_to_market_score" in result.columns else result[
            "book_to_market_score"
        ]

    weighted = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    count = pd.Series(0, index=result.index, dtype="int64")

    for factor, weight in TTM_VALUATION_WEIGHTS.items():
        column = (
            "book_to_market_score"
            if factor == "book_to_market"
            else f"{factor}_score"
        )
        values = _numeric(result, column)
        available = values.notna()
        weighted.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        count.loc[available] += 1

    eligible = count.ge(config.minimum_factors) & available_weight.gt(0)
    score = pd.Series(np.nan, index=result.index, dtype="float64")
    score.loc[eligible] = weighted.loc[eligible] / available_weight.loc[eligible]

    result["ttm_valuation_factor_count"] = count
    result["ttm_valuation_weight_coverage"] = available_weight
    result["ttm_valuation_eligible"] = eligible
    result["ttm_valuation_score"] = score
    return result


def score_weighted_family(
    frame: pd.DataFrame,
    *,
    weights: dict[str, float],
    output_prefix: str,
    minimum_factors: int = 2,
) -> pd.DataFrame:
    """Generic isolated weighted-family scorer for current comparison."""

    result = frame.copy()
    weighted = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    count = pd.Series(0, index=result.index, dtype="int64")

    for factor, weight in weights.items():
        values = _numeric(result, f"{factor}_score")
        available = values.notna()
        weighted.loc[available] += values.loc[available] * weight
        available_weight.loc[available] += weight
        count.loc[available] += 1

    eligible = count.ge(minimum_factors) & available_weight.gt(0)
    score = pd.Series(np.nan, index=result.index, dtype="float64")
    score.loc[eligible] = weighted.loc[eligible] / available_weight.loc[eligible]

    result[f"{output_prefix}_factor_count"] = count
    result[f"{output_prefix}_weight_coverage"] = available_weight
    result[f"{output_prefix}_eligible"] = eligible
    result[f"{output_prefix}_score"] = score
    return result


def _yield_ratio(numerator: pd.Series, market_cap: pd.Series) -> pd.Series:
    values = pd.Series(np.nan, index=market_cap.index, dtype="float64")
    valid = numerator.notna() & market_cap.notna() & market_cap.gt(0)
    values.loc[valid] = numerator.loc[valid] / market_cap.loc[valid]
    values.loc[~np.isfinite(values)] = np.nan
    return values


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _invalidate(
    valid: pd.Series,
    reason: pd.Series,
    mask: pd.Series,
    code: str,
) -> None:
    mask = mask.fillna(False) & valid
    if not mask.any():
        return
    existing = reason.loc[mask]
    reason.loc[mask] = existing.where(existing.eq(""), existing + "|") + code
    valid.loc[mask] = False
