from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .registry import FACTOR_REGISTRY


@dataclass(frozen=True)
class ValidationThresholds:
    balance_sheet_scale_ratio: float = 100.0
    growth_prior_scale_floor: float = 1e-4
    growth_abs_ratio_limit: float = 1e6
    annual_max_age_days: int = 550


DEFAULT_THRESHOLDS = ValidationThresholds()


def validate_raw_factors(
    frame: pd.DataFrame,
    *,
    thresholds: ValidationThresholds = DEFAULT_THRESHOLDS,
) -> pd.DataFrame:
    """Validate factor inputs while preserving raw values for auditability.

    For every registered factor this adds:
      <factor>_valid
      <factor>_invalid_reason
      <factor>_validated

    Raw factor columns are never overwritten.
    """

    result = frame.copy()

    for factor in FACTOR_REGISTRY:
        if factor not in result.columns:
            continue

        raw = pd.to_numeric(result[factor], errors="coerce")
        valid = raw.notna()
        reason = pd.Series("", index=result.index, dtype="object")

        if factor in {
            "return_on_assets",
            "return_on_equity",
            "operating_margin",
            "free_cash_flow_margin",
            "liabilities_to_assets",
            "cash_to_assets",
            "operating_cash_flow_to_liabilities",
        }:
            _apply_ratio_sanity(
                result,
                factor=factor,
                valid=valid,
                reason=reason,
                thresholds=thresholds,
            )

        if factor in {
            "revenue_growth_1y",
            "net_income_growth_1y",
            "operating_income_growth_1y",
            "operating_cash_flow_growth_1y",
        }:
            _apply_growth_sanity(
                result,
                factor=factor,
                valid=valid,
                reason=reason,
                thresholds=thresholds,
            )

        if factor in {
            "earnings_yield_annual",
            "sales_yield_annual",
            "free_cash_flow_yield_annual",
            "book_to_market",
        }:
            _apply_valuation_sanity(
                result,
                factor=factor,
                valid=valid,
                reason=reason,
                thresholds=thresholds,
            )

        result[f"{factor}_valid"] = valid
        result[f"{factor}_invalid_reason"] = reason
        result[f"{factor}_validated"] = raw.where(valid)

    return result


def _apply_ratio_sanity(
    frame: pd.DataFrame,
    *,
    factor: str,
    valid: pd.Series,
    reason: pd.Series,
    thresholds: ValidationThresholds,
) -> None:
    assets = pd.to_numeric(frame.get("total_assets"), errors="coerce")
    liabilities = pd.to_numeric(frame.get("total_liabilities"), errors="coerce")
    cash = pd.to_numeric(frame.get("cash"), errors="coerce")
    equity = pd.to_numeric(frame.get("shareholders_equity"), errors="coerce")
    revenue = pd.to_numeric(frame.get("revenue"), errors="coerce")

    if factor in {"return_on_assets", "liabilities_to_assets", "cash_to_assets"}:
        _invalidate_scale_mismatch(
            valid,
            reason,
            anchor=assets,
            related={
                "total_liabilities": liabilities,
                "cash": cash,
                "shareholders_equity": equity,
            },
            threshold=thresholds.balance_sheet_scale_ratio,
        )

    if factor == "return_on_equity":
        invalid = equity.notna() & (equity <= 0)
        _mark_invalid(valid, reason, invalid, "nonpositive_equity")

    if factor in {
        "operating_margin",
        "free_cash_flow_margin",
    }:
        invalid = revenue.notna() & (revenue <= 0)
        _mark_invalid(valid, reason, invalid, "nonpositive_revenue")

    if factor == "operating_cash_flow_to_liabilities":
        invalid = liabilities.notna() & (liabilities <= 0)
        _mark_invalid(valid, reason, invalid, "nonpositive_liabilities")


def _apply_growth_sanity(
    frame: pd.DataFrame,
    *,
    factor: str,
    valid: pd.Series,
    reason: pd.Series,
    thresholds: ValidationThresholds,
) -> None:
    source = {
        "revenue_growth_1y": "revenue",
        "net_income_growth_1y": "net_income",
        "operating_income_growth_1y": "operating_income",
        "operating_cash_flow_growth_1y": "operating_cash_flow",
    }[factor]

    current = pd.to_numeric(frame.get(source), errors="coerce")
    prior = pd.to_numeric(frame.get(f"{source}_prior_52w"), errors="coerce")
    assets = pd.to_numeric(frame.get("total_assets"), errors="coerce")
    revenue = pd.to_numeric(frame.get("revenue"), errors="coerce")

    # Use the larger of assets/revenue as an economic scale anchor.
    scale = pd.concat([assets.abs(), revenue.abs()], axis=1).max(axis=1)

    tiny_prior = (
        prior.notna()
        & scale.notna()
        & (scale > 0)
        & (prior.abs() / scale < thresholds.growth_prior_scale_floor)
    )
    _mark_invalid(valid, reason, tiny_prior, "prior_value_tiny_vs_company_scale")

    absurd_jump = (
        current.notna()
        & prior.notna()
        & (prior != 0)
        & ((current - prior).abs() / prior.abs() > thresholds.growth_abs_ratio_limit)
    )
    _mark_invalid(valid, reason, absurd_jump, "growth_jump_exceeds_validation_limit")

    if "growth_lookback_valid" in frame.columns:
        bad_lookback = ~frame["growth_lookback_valid"].fillna(False).astype(bool)
        _mark_invalid(valid, reason, bad_lookback, "invalid_growth_lookback")


def _apply_valuation_sanity(
    frame: pd.DataFrame,
    *,
    factor: str,
    valid: pd.Series,
    reason: pd.Series,
    thresholds: ValidationThresholds,
) -> None:
    market_cap = pd.to_numeric(frame.get("market_cap"), errors="coerce")
    invalid_market_cap = market_cap.notna() & (market_cap <= 0)
    _mark_invalid(valid, reason, invalid_market_cap, "nonpositive_market_cap")

    if factor == "sales_yield_annual":
        annual_revenue = pd.to_numeric(
            frame.get("annual_revenue"),
            errors="coerce",
        )
        invalid_revenue = annual_revenue.notna() & (annual_revenue <= 0)
        _mark_invalid(
            valid,
            reason,
            invalid_revenue,
            "nonpositive_annual_revenue",
        )

    if factor == "book_to_market":
        equity = pd.to_numeric(
            frame.get("shareholders_equity"),
            errors="coerce",
        )
        invalid_equity = equity.notna() & (equity <= 0)
        _mark_invalid(valid, reason, invalid_equity, "nonpositive_equity")
        return

    concepts = {
        "earnings_yield_annual": ("annual_net_income",),
        "sales_yield_annual": ("annual_revenue",),
        "free_cash_flow_yield_annual": (
            "annual_operating_cash_flow",
            "annual_capital_expenditures",
        ),
    }[factor]

    decision_date = pd.to_datetime(
        frame.get("decision_date"),
        errors="coerce",
        utc=True,
    ).dt.tz_convert(None)

    for concept in concepts:
        accepted_column = f"{concept}_accepted_at"
        if accepted_column not in frame.columns:
            continue

        accepted = pd.to_datetime(
            frame[accepted_column],
            errors="coerce",
            utc=True,
        ).dt.tz_convert(None)
        age_days = (decision_date - accepted).dt.days

        future = accepted.notna() & decision_date.notna() & (age_days < 0)
        stale = (
            accepted.notna()
            & decision_date.notna()
            & (age_days > thresholds.annual_max_age_days)
        )
        missing_acceptance = (
            pd.to_numeric(frame.get(concept), errors="coerce").notna()
            & accepted.isna()
        )

        _mark_invalid(
            valid,
            reason,
            future,
            f"{concept}_accepted_after_decision",
        )
        _mark_invalid(
            valid,
            reason,
            stale,
            f"{concept}_stale_annual_fact",
        )
        _mark_invalid(
            valid,
            reason,
            missing_acceptance,
            f"{concept}_missing_acceptance",
        )


def _invalidate_scale_mismatch(
    valid: pd.Series,
    reason: pd.Series,
    *,
    anchor: pd.Series,
    related: dict[str, pd.Series],
    threshold: float,
) -> None:
    for name, series in related.items():
        mismatch = (
            anchor.notna()
            & series.notna()
            & (anchor.abs() > 0)
            & (series.abs() / anchor.abs() > threshold)
        )
        _mark_invalid(
            valid,
            reason,
            mismatch,
            f"{name}_scale_mismatch_vs_total_assets",
        )


def _mark_invalid(
    valid: pd.Series,
    reason: pd.Series,
    mask: pd.Series,
    code: str,
) -> None:
    mask = mask.fillna(False) & valid
    if not mask.any():
        return

    existing = reason.loc[mask]
    reason.loc[mask] = existing.where(
        existing == "",
        existing + "|",
    ) + code
    valid.loc[mask] = False
