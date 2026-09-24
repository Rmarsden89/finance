from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


DURATION_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_cash_flow",
    "capital_expenditures",
}
FISCAL_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


@dataclass(frozen=True)
class QuarterReconstructionResult:
    quarters: pd.DataFrame
    audit: pd.DataFrame


@dataclass(frozen=True)
class TtmConstructionResult:
    values: pd.DataFrame
    audit: pd.DataFrame


def build_ttm_values(
    quarters: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
) -> TtmConstructionResult:
    """Build strict four-consecutive-quarter TTM values.

    This implementation is vectorized across each CIK/concept/unit series so
    large historical quarter sets do not require one Python/DataFrame slice per
    candidate endpoint.
    """

    required = {
        "cik",
        "concept",
        "fy",
        "fiscal_quarter",
        "quarter_ordinal",
        "uom",
        "value",
        "quarter_end_date",
        "available_at",
        "derivation",
        "source_adshs",
        "source_tags",
        "source_forms",
        "source_accepted_ats",
        "source_ddate_dates",
    }
    missing = sorted(required - set(quarters.columns))
    if missing:
        raise ValueError(
            "Discrete quarters missing TTM columns: " + ", ".join(missing)
        )

    frame = quarters.copy().reset_index(drop=True)
    frame["fy"] = pd.to_numeric(frame["fy"], errors="raise").astype(int)
    frame["quarter_ordinal"] = pd.to_numeric(
        frame["quarter_ordinal"], errors="raise"
    ).astype(int)
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    frame["quarter_end_date"] = pd.to_datetime(
        frame["quarter_end_date"], errors="raise"
    )
    frame["available_at"] = pd.to_datetime(
        frame["available_at"], errors="raise"
    )
    frame["fiscal_quarter"] = (
        frame["fiscal_quarter"].astype(str).str.upper().str.strip()
    )
    frame["uom"] = frame["uom"].astype(str).str.upper().str.strip()

    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        available_tz = frame["available_at"].dt.tz
        if cutoff.tzinfo is not None and available_tz is None:
            cutoff = cutoff.tz_localize(None)
        elif cutoff.tzinfo is None and available_tz is not None:
            cutoff = cutoff.tz_localize(available_tz)
        elif cutoff.tzinfo is not None and available_tz is not None:
            cutoff = cutoff.tz_convert(available_tz)
        frame = frame.loc[frame["available_at"].le(cutoff)].copy()

    invalid_quarter = ~frame["fiscal_quarter"].isin(FISCAL_QUARTERS)
    if invalid_quarter.any():
        values = sorted(frame.loc[invalid_quarter, "fiscal_quarter"].unique())
        raise ValueError(
            "Unsupported fiscal_quarter values: " + ", ".join(values)
        )

    ordinal_map = {
        quarter: index + 1 for index, quarter in enumerate(FISCAL_QUARTERS)
    }
    expected_ordinal = frame["fiscal_quarter"].map(ordinal_map)
    if frame["quarter_ordinal"].ne(expected_ordinal).any():
        raise ValueError("quarter_ordinal does not match fiscal_quarter")

    quarter_keys = ["cik", "concept", "fy", "fiscal_quarter", "uom"]
    duplicates = frame.duplicated(quarter_keys, keep=False)
    if duplicates.any():
        duplicate_keys = (
            frame.loc[duplicates, quarter_keys]
            .drop_duplicates()
            .astype(str)
            .agg("|".join, axis=1)
            .tolist()
        )
        raise ValueError(
            "Duplicate discrete-quarter keys: " + ", ".join(duplicate_keys[:10])
        )

    group_keys = ["cik", "concept", "uom"]
    frame = frame.sort_values(
        [*group_keys, "quarter_end_date", "fy", "quarter_ordinal"],
        kind="stable",
    ).reset_index(drop=True)
    grouped = frame.groupby(group_keys, sort=False, dropna=False)

    # Encode fiscal quarter as a monotonic integer so exact continuity across
    # fiscal-year boundaries is a simple +1 relationship.
    frame["_fiscal_index"] = frame["fy"] * 4 + frame["quarter_ordinal"]

    for offset in (1, 2, 3):
        for column in (
            "_fiscal_index",
            "quarter_end_date",
            "value",
            "available_at",
            "fy",
            "fiscal_quarter",
            "derivation",
            "source_adshs",
            "source_tags",
            "source_forms",
            "source_accepted_ats",
            "source_ddate_dates",
        ):
            frame[f"_lag{offset}_{column}"] = grouped[column].shift(offset)

    frame["_group_position"] = grouped.cumcount()
    enough_history = frame["_group_position"].ge(3)

    consecutive = enough_history.copy()
    for offset in (1, 2, 3):
        consecutive &= (
            frame["_fiscal_index"] - frame[f"_lag{offset}__fiscal_index"]
        ).eq(offset)

    increasing_dates = (
        frame["_lag3_quarter_end_date"]
        .lt(frame["_lag2_quarter_end_date"])
        & frame["_lag2_quarter_end_date"].lt(
            frame["_lag1_quarter_end_date"]
        )
        & frame["_lag1_quarter_end_date"].lt(frame["quarter_end_date"])
    )
    present_values = frame[
        [
            "_lag3_value",
            "_lag2_value",
            "_lag1_value",
            "value",
        ]
    ].notna().all(axis=1)

    valid = enough_history & consecutive & increasing_dates & present_values
    valid_rows = frame.loc[valid].copy()

    if valid_rows.empty:
        values = pd.DataFrame(columns=[
            "cik", "concept", "uom", "ttm_end_fy", "ttm_end_quarter",
            "ttm_end_date", "ttm_value", "available_at", "quarter_count",
            "quarter_keys", "quarter_end_dates", "quarter_values",
            "quarter_derivations", "source_adshs", "source_tags",
            "source_forms", "source_accepted_ats", "source_ddate_dates",
        ])
    else:
        values = pd.DataFrame({
            "cik": valid_rows["cik"].astype(int),
            "concept": valid_rows["concept"].astype(str),
            "uom": valid_rows["uom"].astype(str),
            "ttm_end_fy": valid_rows["fy"].astype(int),
            "ttm_end_quarter": valid_rows["fiscal_quarter"].astype(str),
            "ttm_end_date": valid_rows["quarter_end_date"].dt.date,
            "ttm_value": (
                valid_rows["_lag3_value"]
                + valid_rows["_lag2_value"]
                + valid_rows["_lag1_value"]
                + valid_rows["value"]
            ).astype(float),
            "available_at": pd.concat(
                [
                    valid_rows["_lag3_available_at"],
                    valid_rows["_lag2_available_at"],
                    valid_rows["_lag1_available_at"],
                    valid_rows["available_at"],
                ],
                axis=1,
            ).max(axis=1),
            "quarter_count": 4,
        })
        values["quarter_keys"] = (
            valid_rows["_lag3_fy"].astype(int).astype(str) + "-"
            + valid_rows["_lag3_fiscal_quarter"].astype(str) + "|"
            + valid_rows["_lag2_fy"].astype(int).astype(str) + "-"
            + valid_rows["_lag2_fiscal_quarter"].astype(str) + "|"
            + valid_rows["_lag1_fy"].astype(int).astype(str) + "-"
            + valid_rows["_lag1_fiscal_quarter"].astype(str) + "|"
            + valid_rows["fy"].astype(int).astype(str) + "-"
            + valid_rows["fiscal_quarter"].astype(str)
        )
        values["quarter_end_dates"] = (
            valid_rows["_lag3_quarter_end_date"].dt.date.astype(str) + "|"
            + valid_rows["_lag2_quarter_end_date"].dt.date.astype(str) + "|"
            + valid_rows["_lag1_quarter_end_date"].dt.date.astype(str) + "|"
            + valid_rows["quarter_end_date"].dt.date.astype(str)
        )
        values["quarter_values"] = (
            valid_rows["_lag3_value"].astype(float).astype(str) + "|"
            + valid_rows["_lag2_value"].astype(float).astype(str) + "|"
            + valid_rows["_lag1_value"].astype(float).astype(str) + "|"
            + valid_rows["value"].astype(float).astype(str)
        )
        for output_column, source_column in (
            ("quarter_derivations", "derivation"),
            ("source_adshs", "source_adshs"),
            ("source_tags", "source_tags"),
            ("source_forms", "source_forms"),
            ("source_accepted_ats", "source_accepted_ats"),
            ("source_ddate_dates", "source_ddate_dates"),
        ):
            separator = "|" if output_column == "quarter_derivations" else "||"
            values[output_column] = (
                valid_rows[f"_lag3_{source_column}"].astype(str)
                + separator
                + valid_rows[f"_lag2_{source_column}"].astype(str)
                + separator
                + valid_rows[f"_lag1_{source_column}"].astype(str)
                + separator
                + valid_rows[source_column].astype(str)
            )
        values = values.sort_values(
            ["cik", "concept", "ttm_end_date", "uom"],
            kind="stable",
        ).reset_index(drop=True)

    audit_reason = pd.Series(pd.NA, index=frame.index, dtype="object")
    audit_reason.loc[~enough_history] = "insufficient_prior_quarters"
    audit_reason.loc[
        enough_history & ~consecutive
    ] = "nonconsecutive_fiscal_quarters"
    audit_reason.loc[
        enough_history & consecutive & ~increasing_dates
    ] = "nonincreasing_quarter_end_dates"
    audit_reason.loc[
        enough_history & consecutive & increasing_dates & ~present_values
    ] = "missing_quarter_value"

    audited = frame.loc[audit_reason.notna()].copy()
    audit = pd.DataFrame({
        "cik": audited["cik"].astype(int),
        "concept": audited["concept"].astype(str),
        "uom": audited["uom"].astype(str),
        "ttm_end_fy": audited["fy"].astype(int),
        "ttm_end_quarter": audited["fiscal_quarter"].astype(str),
        "ttm_end_date": audited["quarter_end_date"].dt.date,
        "reason": audit_reason.loc[audited.index].astype(str),
    })
    if not audit.empty:
        audit = audit.sort_values(
            ["cik", "concept", "ttm_end_date", "uom"],
            kind="stable",
        ).reset_index(drop=True)

    return TtmConstructionResult(values=values, audit=audit)

def _next_fiscal_quarter(key: tuple[int, int]) -> tuple[int, int]:
    fy, ordinal = key
    if ordinal < 4:
        return fy, ordinal + 1
    return fy + 1, 1


def _ttm_audit_row(
    *,
    cik: object,
    concept: object,
    uom: object,
    end_row: pd.Series,
    reason: str,
) -> dict[str, object]:
    return {
        "cik": int(cik),
        "concept": str(concept),
        "uom": str(uom),
        "ttm_end_fy": int(end_row.fy),
        "ttm_end_quarter": str(end_row.fiscal_quarter),
        "ttm_end_date": end_row.quarter_end_date.date(),
        "reason": reason,
    }


def reconstruct_discrete_quarters_as_of(
    winner_facts: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    income_quarter_policy: str = "direct_preferred",
) -> QuarterReconstructionResult:
    """Reconstruct PIT-visible discrete fiscal quarters from SEC duration facts.

    The input is the canonical winner-fact cache. Only facts accepted on or
    before the as-of timestamp are eligible. For revenue and net income,
    income_quarter_policy controls whether compatible YTD-derived Q2/Q3 values
    are preferred over directly reported qtrs=1 observations. Q4 is always
    reconstructed as annual minus Q3 YTD.

    Compatibility is deliberately strict: CIK, canonical concept, fiscal year,
    and unit must match. Missing components remain missing rather than being
    filled from another fiscal year, unit, or concept.
    """

    allowed_policies = {"direct_preferred", "ytd_preferred"}
    if income_quarter_policy not in allowed_policies:
        raise ValueError(
            "Unsupported income_quarter_policy: "
            f"{income_quarter_policy}. Expected one of "
            + ", ".join(sorted(allowed_policies))
        )

    required = {
        "cik",
        "concept",
        "value",
        "uom",
        "fy",
        "fp",
        "qtrs",
        "ddate_date",
        "accepted_at",
        "adsh",
        "source_tag",
        "form",
    }
    missing = sorted(required - set(winner_facts.columns))
    if missing:
        raise ValueError(
            "Winner facts missing TTM reconstruction columns: "
            + ", ".join(missing)
        )

    facts = winner_facts.copy().reset_index(drop=True)
    facts["_input_order"] = range(len(facts))
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"], errors="raise"
    )
    cutoff = pd.Timestamp(as_of)
    accepted_tz = facts["accepted_at"].dt.tz
    if cutoff.tzinfo is not None and accepted_tz is None:
        cutoff = cutoff.tz_localize(None)
    elif cutoff.tzinfo is None and accepted_tz is not None:
        cutoff = cutoff.tz_localize(accepted_tz)
    elif cutoff.tzinfo is not None and accepted_tz is not None:
        cutoff = cutoff.tz_convert(accepted_tz)

    facts["ddate_date"] = pd.to_datetime(
        facts["ddate_date"], errors="raise"
    ).dt.date
    facts["qtrs"] = pd.to_numeric(facts["qtrs"], errors="raise")
    facts["value"] = pd.to_numeric(facts["value"], errors="raise")
    facts["fy"] = pd.to_numeric(facts["fy"], errors="coerce")
    facts["fp"] = facts["fp"].astype(str).str.upper().str.strip()
    facts["uom"] = facts["uom"].astype(str).str.upper().str.strip()

    facts = facts.loc[
        facts["concept"].isin(DURATION_CONCEPTS)
        & facts["accepted_at"].le(cutoff)
        & facts["fy"].notna()
        & facts["fp"].isin({"Q1", "Q2", "Q3", "FY"})
        & facts["qtrs"].isin({1, 2, 3, 4})
        & facts["uom"].ne("")
    ].copy()
    facts["fy"] = facts["fy"].astype(int)

    # Amendments and later accepted versions replace earlier versions of the
    # same fiscal-period representation only after they become PIT-visible.
    selection_keys = ["cik", "concept", "fy", "fp", "qtrs", "uom"]
    facts = (
        facts.sort_values(
            [*selection_keys, "accepted_at", "_input_order"],
            kind="stable",
        )
        .drop_duplicates(selection_keys, keep="last")
        .reset_index(drop=True)
    )

    quarter_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []

    for key, group in facts.groupby(
        ["cik", "concept", "fy", "uom"],
        sort=False,
        dropna=False,
    ):
        cik, concept, fy, uom = key
        selected: dict[tuple[str, int], pd.Series] = {
            (str(row.fp), int(row.qtrs)): row
            for _, row in group.iterrows()
        }

        q1 = selected.get(("Q1", 1))
        if q1 is not None:
            quarter_rows.append(
                _direct_quarter(q1, fiscal_quarter="Q1")
            )
        else:
            audit_rows.append(
                _audit(cik, concept, fy, uom, "Q1", "missing_direct_q1")
            )

        q2_direct = selected.get(("Q2", 1))
        q2_ytd = selected.get(("Q2", 2))
        prefer_ytd = (
            concept in {"revenue", "net_income"}
            and income_quarter_policy == "ytd_preferred"
        )
        q2_derived_available = q1 is not None and q2_ytd is not None

        if prefer_ytd and q2_derived_available:
            if q1.ddate_date >= q2_ytd.ddate_date:
                audit_rows.append(
                    _audit(
                        cik, concept, fy, uom, "Q2",
                        "nonincreasing_period_ends",
                    )
                )
            else:
                quarter_rows.append(
                    _derived_quarter(
                        fiscal_quarter="Q2",
                        minuend=q2_ytd,
                        subtrahend=q1,
                        formula="q2_ytd_minus_q1",
                    )
                )
        elif q2_direct is not None:
            quarter_rows.append(
                _direct_quarter(q2_direct, fiscal_quarter="Q2")
            )
        elif q2_derived_available:
            if q1.ddate_date >= q2_ytd.ddate_date:
                audit_rows.append(
                    _audit(
                        cik, concept, fy, uom, "Q2",
                        "nonincreasing_period_ends",
                    )
                )
            else:
                quarter_rows.append(
                    _derived_quarter(
                        fiscal_quarter="Q2",
                        minuend=q2_ytd,
                        subtrahend=q1,
                        formula="q2_ytd_minus_q1",
                    )
                )
        else:
            audit_rows.append(
                _audit(
                    cik, concept, fy, uom, "Q2",
                    _missing_reason(
                        ("q1", q1),
                        ("q2_ytd", q2_ytd),
                    ),
                )
            )

        q3_direct = selected.get(("Q3", 1))
        q3_ytd = selected.get(("Q3", 3))
        q3_derived_available = q2_ytd is not None and q3_ytd is not None

        if prefer_ytd and q3_derived_available:
            if q2_ytd.ddate_date >= q3_ytd.ddate_date:
                audit_rows.append(
                    _audit(
                        cik, concept, fy, uom, "Q3",
                        "nonincreasing_period_ends",
                    )
                )
            else:
                quarter_rows.append(
                    _derived_quarter(
                        fiscal_quarter="Q3",
                        minuend=q3_ytd,
                        subtrahend=q2_ytd,
                        formula="q3_ytd_minus_q2_ytd",
                    )
                )
        elif q3_direct is not None:
            quarter_rows.append(
                _direct_quarter(q3_direct, fiscal_quarter="Q3")
            )
        elif q3_derived_available:
            if q2_ytd.ddate_date >= q3_ytd.ddate_date:
                audit_rows.append(
                    _audit(
                        cik, concept, fy, uom, "Q3",
                        "nonincreasing_period_ends",
                    )
                )
            else:
                quarter_rows.append(
                    _derived_quarter(
                        fiscal_quarter="Q3",
                        minuend=q3_ytd,
                        subtrahend=q2_ytd,
                        formula="q3_ytd_minus_q2_ytd",
                    )
                )
        else:
            audit_rows.append(
                _audit(
                    cik, concept, fy, uom, "Q3",
                    _missing_reason(
                        ("q2_ytd", q2_ytd),
                        ("q3_ytd", q3_ytd),
                    ),
                )
            )

        annual = selected.get(("FY", 4))
        if annual is not None and q3_ytd is not None:
            if q3_ytd.ddate_date >= annual.ddate_date:
                audit_rows.append(
                    _audit(
                        cik, concept, fy, uom, "Q4",
                        "nonincreasing_period_ends",
                    )
                )
            else:
                quarter_rows.append(
                    _derived_quarter(
                        fiscal_quarter="Q4",
                        minuend=annual,
                        subtrahend=q3_ytd,
                        formula="annual_minus_q3_ytd",
                    )
                )
        else:
            audit_rows.append(
                _audit(
                    cik, concept, fy, uom, "Q4",
                    _missing_reason(
                        ("annual", annual),
                        ("q3_ytd", q3_ytd),
                    ),
                )
            )

    quarter_columns = [
        "cik",
        "concept",
        "fy",
        "fiscal_quarter",
        "quarter_ordinal",
        "uom",
        "value",
        "quarter_end_date",
        "available_at",
        "derivation",
        "source_count",
        "source_adshs",
        "source_tags",
        "source_forms",
        "source_accepted_ats",
        "source_ddate_dates",
    ]
    audit_columns = [
        "cik",
        "concept",
        "fy",
        "uom",
        "fiscal_quarter",
        "reason",
    ]
    quarters = pd.DataFrame(quarter_rows, columns=quarter_columns)
    audit = pd.DataFrame(audit_rows, columns=audit_columns)
    if not quarters.empty:
        quarters = quarters.sort_values(
            ["cik", "concept", "fy", "quarter_ordinal", "uom"],
            kind="stable",
        ).reset_index(drop=True)
    if not audit.empty:
        audit = audit.sort_values(
            ["cik", "concept", "fy", "fiscal_quarter", "uom"],
            kind="stable",
        ).reset_index(drop=True)
    return QuarterReconstructionResult(quarters=quarters, audit=audit)


def _direct_quarter(
    row: pd.Series,
    *,
    fiscal_quarter: str,
) -> dict[str, object]:
    return {
        "cik": int(row.cik),
        "concept": str(row.concept),
        "fy": int(row.fy),
        "fiscal_quarter": fiscal_quarter,
        "quarter_ordinal": FISCAL_QUARTERS.index(fiscal_quarter) + 1,
        "uom": str(row.uom),
        "value": float(row.value),
        "quarter_end_date": row.ddate_date,
        "available_at": row.accepted_at,
        "derivation": "direct_qtrs_1",
        "source_count": 1,
        "source_adshs": str(row.adsh),
        "source_tags": str(row.source_tag),
        "source_forms": str(row.form),
        "source_accepted_ats": row.accepted_at.isoformat(),
        "source_ddate_dates": row.ddate_date.isoformat(),
    }


def _derived_quarter(
    *,
    fiscal_quarter: str,
    minuend: pd.Series,
    subtrahend: pd.Series,
    formula: str,
) -> dict[str, object]:
    sources = [subtrahend, minuend]
    return {
        "cik": int(minuend.cik),
        "concept": str(minuend.concept),
        "fy": int(minuend.fy),
        "fiscal_quarter": fiscal_quarter,
        "quarter_ordinal": FISCAL_QUARTERS.index(fiscal_quarter) + 1,
        "uom": str(minuend.uom),
        "value": float(minuend.value - subtrahend.value),
        "quarter_end_date": minuend.ddate_date,
        "available_at": max(source.accepted_at for source in sources),
        "derivation": formula,
        "source_count": 2,
        "source_adshs": "|".join(str(source.adsh) for source in sources),
        "source_tags": "|".join(str(source.source_tag) for source in sources),
        "source_forms": "|".join(str(source.form) for source in sources),
        "source_accepted_ats": "|".join(
            source.accepted_at.isoformat() for source in sources
        ),
        "source_ddate_dates": "|".join(
            source.ddate_date.isoformat() for source in sources
        ),
    }


def _audit(
    cik: object,
    concept: object,
    fy: object,
    uom: object,
    fiscal_quarter: str,
    reason: str,
) -> dict[str, object]:
    return {
        "cik": int(cik),
        "concept": str(concept),
        "fy": int(fy),
        "uom": str(uom),
        "fiscal_quarter": fiscal_quarter,
        "reason": reason,
    }


def _missing_reason(*parts: tuple[str, pd.Series | None]) -> str:
    missing = [name for name, row in parts if row is None]
    return "missing_" + "_and_".join(missing)
