from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd

from .sec_snapshot import latest_facts_as_of


PROVENANCE_COLUMNS = [
    "value",
    "ddate_date",
    "period_date",
    "filed_date",
    "accepted_at",
    "form",
    "fy",
    "fp",
    "qtrs",
    "uom",
    "source_tag",
    "adsh",
    "source_system",
]


@dataclass(frozen=True)
class SecShadowImpactSummary:
    universe_members: int
    universe_members_with_cik: int
    historical_latest_fact_rows: int
    shadow_latest_fact_rows: int
    changed_fact_groups: int
    fresher_period_groups: int
    same_period_newer_filing_groups: int
    newly_available_groups: int
    value_changed_groups: int
    provenance_only_groups: int
    tickers_with_any_change: int
    tickers_with_fresher_period: int
    ambiguous_rejection_groups: int


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["cik"] = pd.to_numeric(result["cik"], errors="coerce")
    if "accepted_at" in result.columns:
        result["accepted_at"] = pd.to_datetime(result["accepted_at"], errors="coerce")
    for column in ("ddate_date", "period_date", "filed_date"):
        if column in result.columns:
            result[column] = pd.to_datetime(result[column], errors="coerce").dt.date
    return result


def _decimal_equal(left, right) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    if pd.isna(left) or pd.isna(right):
        return False
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def compare_sec_latest_state(
    historical_winners: pd.DataFrame,
    shadow_winners: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    universe: pd.DataFrame,
    merge_audit: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, SecShadowImpactSummary]:
    historical = _normalize(historical_winners)
    shadow = _normalize(shadow_winners)
    cutoff = pd.Timestamp(as_of).to_pydatetime()

    hist_latest = latest_facts_as_of(historical, cutoff)
    shadow_latest = latest_facts_as_of(shadow, cutoff)

    universe_frame = universe.copy()
    universe_frame["cik"] = pd.to_numeric(universe_frame["cik"], errors="coerce")
    universe_frame = universe_frame.drop_duplicates(subset=["ticker", "cik"])
    valid_ciks = set(universe_frame["cik"].dropna().astype(int))

    hist_latest = hist_latest.loc[hist_latest["cik"].isin(valid_ciks)].copy()
    shadow_latest = shadow_latest.loc[shadow_latest["cik"].isin(valid_ciks)].copy()

    ticker_map = (
        universe_frame.dropna(subset=["cik"])
        .assign(cik=lambda frame: frame["cik"].astype(int))
        .set_index("cik")["ticker"]
        .to_dict()
    )

    keys = ["cik", "concept"]
    hist_lookup = {
        tuple(row[key] for key in keys): row
        for _, row in hist_latest.iterrows()
    }
    shadow_lookup = {
        tuple(row[key] for key in keys): row
        for _, row in shadow_latest.iterrows()
    }

    detail_rows: list[dict] = []
    for key in sorted(set(hist_lookup) | set(shadow_lookup)):
        old = hist_lookup.get(key)
        new = shadow_lookup.get(key)

        old_value = None if old is None else old.get("value")
        new_value = None if new is None else new.get("value")
        old_period = None if old is None else old.get("ddate_date")
        new_period = None if new is None else new.get("ddate_date")
        old_accepted = None if old is None else old.get("accepted_at")
        new_accepted = None if new is None else new.get("accepted_at")

        if old is None and new is not None:
            status = "newly_available"
        elif old is not None and new is None:
            status = "missing_in_shadow"
        else:
            value_equal = _decimal_equal(old_value, new_value)
            period_equal = old_period == new_period
            accepted_equal = (
                pd.Timestamp(old_accepted) == pd.Timestamp(new_accepted)
                if pd.notna(old_accepted) and pd.notna(new_accepted)
                else pd.isna(old_accepted) and pd.isna(new_accepted)
            )

            if new_period is not None and old_period is not None and new_period > old_period:
                status = "fresher_period"
            elif (
                period_equal
                and pd.notna(old_accepted)
                and pd.notna(new_accepted)
                and pd.Timestamp(new_accepted) > pd.Timestamp(old_accepted)
            ):
                status = "same_period_newer_filing"
            elif not value_equal:
                status = "value_changed"
            elif not accepted_equal:
                status = "provenance_only"
            else:
                continue

        row = {
            "ticker": ticker_map.get(int(key[0]), ""),
            "cik": int(key[0]),
            "concept": key[1],
            "status": status,
        }

        for column in PROVENANCE_COLUMNS:
            row[f"historical_{column}"] = "" if old is None else old.get(column, "")
            row[f"shadow_{column}"] = "" if new is None else new.get(column, "")

        if old is not None and new is not None:
            if old_period is not None and new_period is not None:
                row["period_days_newer"] = (new_period - old_period).days
            else:
                row["period_days_newer"] = ""
            if pd.notna(old_accepted) and pd.notna(new_accepted):
                row["acceptance_hours_newer"] = (
                    pd.Timestamp(new_accepted) - pd.Timestamp(old_accepted)
                ).total_seconds() / 3600
            else:
                row["acceptance_hours_newer"] = ""
        else:
            row["period_days_newer"] = ""
            row["acceptance_hours_newer"] = ""

        detail_rows.append(row)

    detail = pd.DataFrame(detail_rows)

    ambiguity = pd.DataFrame()
    if merge_audit is not None and not merge_audit.empty:
        audit = merge_audit.copy()
        if "cik" in audit.columns:
            audit["cik"] = pd.to_numeric(audit["cik"], errors="coerce")
        ambiguity = audit.loc[
            audit["status"].astype(str).eq("rejected_ambiguous_value_group")
            & audit["cik"].isin(valid_ciks)
        ].copy()

        if not ambiguity.empty:
            group_cols = [
                column
                for column in ("ticker", "cik", "adsh", "concept", "ddate_date", "qtrs", "uom")
                if column in ambiguity.columns
            ]
            agg = {
                "candidate_rows": ("status", "size"),
            }
            if "value" in ambiguity.columns:
                agg["candidate_values"] = (
                    "value",
                    lambda values: "|".join(sorted(set(values.astype(str)))),
                )
            if "source_tag" in ambiguity.columns:
                agg["candidate_tags"] = (
                    "source_tag",
                    lambda values: "|".join(sorted(set(values.astype(str)))),
                )
            ambiguity = ambiguity.groupby(group_cols, dropna=False).agg(**agg).reset_index()

    if detail.empty:
        ticker_summary = pd.DataFrame(
            columns=[
                "ticker",
                "cik",
                "changed_fact_groups",
                "fresher_period_groups",
                "newly_available_groups",
                "value_changed_groups",
                "latest_shadow_acceptance",
            ]
        )
    else:
        ticker_summary = (
            detail.groupby(["ticker", "cik"], dropna=False)
            .agg(
                changed_fact_groups=("status", "size"),
                fresher_period_groups=("status", lambda values: int((values == "fresher_period").sum())),
                same_period_newer_filing_groups=("status", lambda values: int((values == "same_period_newer_filing").sum())),
                newly_available_groups=("status", lambda values: int((values == "newly_available").sum())),
                value_changed_groups=("status", lambda values: int((values == "value_changed").sum())),
                provenance_only_groups=("status", lambda values: int((values == "provenance_only").sum())),
                latest_shadow_acceptance=("shadow_accepted_at", "max"),
            )
            .reset_index()
            .sort_values(
                ["fresher_period_groups", "changed_fact_groups", "ticker"],
                ascending=[False, False, True],
                kind="stable",
            )
        )

    status_counts = detail["status"].value_counts().to_dict() if not detail.empty else {}
    summary = SecShadowImpactSummary(
        universe_members=len(universe_frame),
        universe_members_with_cik=int(universe_frame["cik"].notna().sum()),
        historical_latest_fact_rows=len(hist_latest),
        shadow_latest_fact_rows=len(shadow_latest),
        changed_fact_groups=len(detail),
        fresher_period_groups=status_counts.get("fresher_period", 0),
        same_period_newer_filing_groups=status_counts.get("same_period_newer_filing", 0),
        newly_available_groups=status_counts.get("newly_available", 0),
        value_changed_groups=status_counts.get("value_changed", 0),
        provenance_only_groups=status_counts.get("provenance_only", 0),
        tickers_with_any_change=int(detail["ticker"].nunique()) if not detail.empty else 0,
        tickers_with_fresher_period=int(
            detail.loc[detail["status"].eq("fresher_period"), "ticker"].nunique()
        ) if not detail.empty else 0,
        ambiguous_rejection_groups=len(ambiguity),
    )

    return detail, ticker_summary, summary, ambiguity
