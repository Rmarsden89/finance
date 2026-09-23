from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd


RISK_STATUSES = {
    "partial_boundary_coverage",
    "missing",
    "provider_error",
}


def membership_days_by_ticker_and_year(
    intervals,
    *,
    start: date,
    end: date,
) -> tuple[dict[str, int], dict[str, dict[int, int]]]:
    """Return calendar membership-day exposure by ticker and year."""

    end_exclusive = end + timedelta(days=1)
    total_days: dict[str, int] = defaultdict(int)
    yearly_days: dict[str, dict[int, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for interval in intervals:
        ticker = interval.ticker.upper()
        interval_end = interval.end_date or end_exclusive
        left = max(interval.start_date, start)
        right = min(interval_end, end_exclusive)
        if left >= right:
            continue

        total_days[ticker] += (right - left).days
        for year in range(left.year, right.year + 1):
            year_left = max(left, date(year, 1, 1))
            year_right = min(right, date(year + 1, 1, 1))
            if year_left < year_right:
                yearly_days[ticker][year] += (year_right - year_left).days

    return dict(total_days), {
        ticker: dict(values)
        for ticker, values in yearly_days.items()
    }


def identity_summary_by_ticker(panel: pd.DataFrame) -> pd.DataFrame:
    """Summarize panel identity resolution independently from price coverage."""

    required = {"ticker", "identity_resolved"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Research panel lacks identity columns: "
            + ", ".join(sorted(missing))
        )

    frame = panel[["ticker", "identity_resolved"]].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["identity_resolved"] = (
        frame["identity_resolved"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes"})
    )
    result = (
        frame.groupby("ticker", as_index=False)
        .agg(
            panel_rows=("identity_resolved", "size"),
            identity_resolved_rows=("identity_resolved", "sum"),
        )
    )
    result["identity_unresolved_rows"] = (
        result["panel_rows"] - result["identity_resolved_rows"]
    )
    result["identity_resolved_fraction"] = (
        result["identity_resolved_rows"] / result["panel_rows"]
    )
    result["identity_status"] = result.apply(
        lambda row: (
            "identity_resolved"
            if row["identity_unresolved_rows"] == 0
            else "identity_unresolved"
            if row["identity_resolved_rows"] == 0
            else "identity_partial"
        ),
        axis=1,
    )
    return result


def classify_unresolved_price_row(row: pd.Series) -> str:
    """Classify unresolved market data without conflating identity and provider gaps."""

    identity = str(row.get("identity_status") or "identity_unknown")
    tiingo = str(row.get("tiingo_status") or "").strip()
    stooq = str(row.get("stooq_status") or "").strip()
    selected_status = str(row.get("selected_status") or "").strip()
    stooq_exclusion = str(
        row.get("stooq_exclusion_reason") or ""
    ).strip()

    if identity in {"identity_unresolved", "identity_partial", "identity_unknown"}:
        identity_class = identity
    else:
        identity_class = "identity_resolved"

    if stooq == "quality_excluded" or stooq_exclusion:
        price_class = "provider_quality_excluded"
    elif (
        tiingo == "partial_boundary_coverage"
        or stooq == "partial_boundary_coverage"
        or selected_status == "partial_boundary_coverage"
    ):
        price_class = "provider_partial_coverage"
    elif (
        tiingo in {"missing", "provider_error", ""}
        and stooq in {"missing", "not_checked", "provider_error", ""}
    ):
        price_class = "provider_missing"
    else:
        price_class = "provider_unresolved_other"

    return f"{identity_class}|{price_class}"


def yearly_panel_coverage(panel: pd.DataFrame) -> pd.DataFrame:
    """Build baseline yearly research-panel availability statistics."""

    required = {
        "decision_date",
        "price_available",
        "research_ready",
        "identity_resolved",
    }
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Research panel lacks coverage columns: "
            + ", ".join(sorted(missing))
        )

    frame = panel.copy()
    frame["year"] = pd.to_datetime(
        frame["decision_date"], errors="coerce"
    ).dt.year

    for column in ("price_available", "research_ready", "identity_resolved"):
        frame[column] = (
            frame[column]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
        )

    rows: list[dict[str, object]] = []
    for year, group in frame.dropna(subset=["year"]).groupby("year", sort=True):
        total = len(group)
        rows.append({
            "year": int(year),
            "panel_rows": total,
            "identity_resolved_rows": int(group["identity_resolved"].sum()),
            "identity_resolved_pct": (
                float(group["identity_resolved"].mean()) if total else 0.0
            ),
            "price_available_rows": int(group["price_available"].sum()),
            "price_available_pct": (
                float(group["price_available"].mean()) if total else 0.0
            ),
            "research_ready_rows": int(group["research_ready"].sum()),
            "research_ready_pct": (
                float(group["research_ready"].mean()) if total else 0.0
            ),
        })

    total = len(frame)
    rows.append({
        "year": "ALL",
        "panel_rows": total,
        "identity_resolved_rows": int(frame["identity_resolved"].sum()),
        "identity_resolved_pct": (
            float(frame["identity_resolved"].mean()) if total else 0.0
        ),
        "price_available_rows": int(frame["price_available"].sum()),
        "price_available_pct": (
            float(frame["price_available"].mean()) if total else 0.0
        ),
        "research_ready_rows": int(frame["research_ready"].sum()),
        "research_ready_pct": (
            float(frame["research_ready"].mean()) if total else 0.0
        ),
    })
    return pd.DataFrame(rows)
