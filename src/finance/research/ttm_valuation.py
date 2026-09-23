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

    TTM windows are only emitted when four compatible discrete fiscal quarters
    exist for the same CIK, concept, and unit with exact fiscal-quarter
    continuity. No annual fallback, interpolation, or gap filling is allowed.
    The TTM availability timestamp is the latest availability timestamp among
    the four constituent quarters.
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

    expected_ordinal = frame["fiscal_quarter"].map(
        {quarter: index + 1 for index, quarter in enumerate(FISCAL_QUARTERS)}
    )
    mismatched_ordinal = frame["quarter_ordinal"].ne(expected_ordinal)
    if mismatched_ordinal.any():
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

    value_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    group_keys = ["cik", "concept", "uom"]

    for key, group in frame.groupby(group_keys, sort=False, dropna=False):
        cik, concept, uom = key
        ordered = group.sort_values(
            ["quarter_end_date", "fy", "quarter_ordinal"],
            kind="stable",
        ).reset_index(drop=True)

        for end_index in range(len(ordered)):
            end_row = ordered.iloc[end_index]
            if end_index < 3:
                audit_rows.append(
                    _ttm_audit_row(
                        cik=cik,
                        concept=concept,
                        uom=uom,
                        end_row=end_row,
                        reason="insufficient_prior_quarters",
                    )
                )
                continue

            window = ordered.iloc[end_index - 3:end_index + 1].copy()
            if not window["quarter_end_date"].is_monotonic_increasing:
                audit_rows.append(
                    _ttm_audit_row(
                        cik=cik,
                        concept=concept,
                        uom=uom,
                        end_row=end_row,
                        reason="nonincreasing_quarter_end_dates",
                    )
                )
                continue

            fiscal_keys = [
                (int(row.fy), int(row.quarter_ordinal))
                for row in window.itertuples()
            ]
            if not all(
                _next_fiscal_quarter(left) == right
                for left, right in zip(fiscal_keys, fiscal_keys[1:])
            ):
                audit_rows.append(
                    _ttm_audit_row(
                        cik=cik,
                        concept=concept,
                        uom=uom,
                        end_row=end_row,
                        reason="nonconsecutive_fiscal_quarters",
                    )
                )
                continue

            values = pd.to_numeric(window["value"], errors="coerce")
            if values.isna().any():
                audit_rows.append(
                    _ttm_audit_row(
                        cik=cik,
                        concept=concept,
                        uom=uom,
                        end_row=end_row,
                        reason="missing_quarter_value",
                    )
                )
                continue

            available_at = window["available_at"].max()
            value_rows.append(
                {
                    "cik": int(cik),
                    "concept": str(concept),
                    "uom": str(uom),
                    "ttm_end_fy": int(end_row.fy),
                    "ttm_end_quarter": str(end_row.fiscal_quarter),
                    "ttm_end_date": end_row.quarter_end_date.date(),
                    "ttm_value": float(values.sum()),
                    "available_at": available_at,
                    "quarter_count": 4,
                    "quarter_keys": "|".join(
                        f"{int(row.fy)}-{row.fiscal_quarter}"
                        for row in window.itertuples()
                    ),
                    "quarter_end_dates": "|".join(
                        row.quarter_end_date.date().isoformat()
                        for row in window.itertuples()
                    ),
                    "quarter_values": "|".join(
                        str(float(row.value)) for row in window.itertuples()
                    ),
                    "quarter_derivations": "|".join(
                        str(row.derivation) for row in window.itertuples()
                    ),
                    "source_adshs": "||".join(
                        str(row.source_adshs) for row in window.itertuples()
                    ),
                    "source_tags": "||".join(
                        str(row.source_tags) for row in window.itertuples()
                    ),
                    "source_forms": "||".join(
                        str(row.source_forms) for row in window.itertuples()
                    ),
                    "source_accepted_ats": "||".join(
                        str(row.source_accepted_ats)
                        for row in window.itertuples()
                    ),
                    "source_ddate_dates": "||".join(
                        str(row.source_ddate_dates)
                        for row in window.itertuples()
                    ),
                }
            )

    value_columns = [
        "cik",
        "concept",
        "uom",
        "ttm_end_fy",
        "ttm_end_quarter",
        "ttm_end_date",
        "ttm_value",
        "available_at",
        "quarter_count",
        "quarter_keys",
        "quarter_end_dates",
        "quarter_values",
        "quarter_derivations",
        "source_adshs",
        "source_tags",
        "source_forms",
        "source_accepted_ats",
        "source_ddate_dates",
    ]
    audit_columns = [
        "cik",
        "concept",
        "uom",
        "ttm_end_fy",
        "ttm_end_quarter",
        "ttm_end_date",
        "reason",
    ]
    values = pd.DataFrame(value_rows, columns=value_columns)
    audit = pd.DataFrame(audit_rows, columns=audit_columns)
    if not values.empty:
        values = values.sort_values(
            ["cik", "concept", "ttm_end_date", "uom"],
            kind="stable",
        ).reset_index(drop=True)
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
) -> QuarterReconstructionResult:
    """Reconstruct PIT-visible discrete fiscal quarters from SEC duration facts.

    The input is the canonical winner-fact cache. Only facts accepted on or
    before the as-of timestamp are eligible. Direct qtrs=1 Q1/Q2/Q3
    observations are preferred when present. Otherwise Q2/Q3 are reconstructed
    from compatible YTD duration facts, and Q4 is reconstructed as annual
    minus Q3 YTD.

    Compatibility is deliberately strict: CIK, canonical concept, fiscal year,
    and unit must match. Missing components remain missing rather than being
    filled from another fiscal year, unit, or concept.
    """

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
        if q2_direct is not None:
            quarter_rows.append(
                _direct_quarter(q2_direct, fiscal_quarter="Q2")
            )
        elif q1 is not None and q2_ytd is not None:
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
        if q3_direct is not None:
            quarter_rows.append(
                _direct_quarter(q3_direct, fiscal_quarter="Q3")
            )
        elif q2_ytd is not None and q3_ytd is not None:
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
