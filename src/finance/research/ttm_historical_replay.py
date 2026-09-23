from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from finance.research.pit_reconciliation import eastern_timestamp


FISCAL_ORDINAL = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
DURATION_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_cash_flow",
    "capital_expenditures",
}


@dataclass(frozen=True)
class HistoricalTtmReplayResult:
    panel: pd.DataFrame
    ttm_events: pd.DataFrame
    event_summary: dict[str, int]


def _quarter_key(fy: int, quarter: str) -> int:
    return int(fy) * 4 + FISCAL_ORDINAL[str(quarter)]


def _quarter_from_index(value: int) -> tuple[int, str]:
    fy, rem = divmod(int(value) - 1, 4)
    ordinal = rem + 1
    return fy, f"Q{ordinal}"


def _lineage(row: pd.Series) -> dict[str, object]:
    return {
        "adsh": str(row.adsh),
        "source_tag": str(row.source_tag),
        "form": str(row.form),
        "accepted_at": pd.Timestamp(row.accepted_at),
        "ddate_date": pd.Timestamp(row.ddate_date),
    }


def _derived_state(
    *,
    fy: int,
    quarter: str,
    minuend: pd.Series,
    subtrahend: pd.Series,
    formula: str,
) -> dict[str, object] | None:
    min_date = pd.Timestamp(minuend.ddate_date)
    sub_date = pd.Timestamp(subtrahend.ddate_date)
    if sub_date >= min_date:
        return None
    accepted = max(
        pd.Timestamp(minuend.accepted_at),
        pd.Timestamp(subtrahend.accepted_at),
    )
    return {
        "fy": int(fy),
        "fiscal_quarter": quarter,
        "quarter_ordinal": FISCAL_ORDINAL[quarter],
        "value": float(minuend.value) - float(subtrahend.value),
        "quarter_end_date": min_date,
        "available_at": accepted,
        "derivation": formula,
        "source_adshs": f"{subtrahend.adsh}|{minuend.adsh}",
        "source_tags": f"{subtrahend.source_tag}|{minuend.source_tag}",
        "source_forms": f"{subtrahend.form}|{minuend.form}",
        "source_accepted_ats": (
            f"{pd.Timestamp(subtrahend.accepted_at).isoformat()}|"
            f"{pd.Timestamp(minuend.accepted_at).isoformat()}"
        ),
    }


def _direct_state(
    *,
    fy: int,
    quarter: str,
    row: pd.Series,
) -> dict[str, object]:
    return {
        "fy": int(fy),
        "fiscal_quarter": quarter,
        "quarter_ordinal": FISCAL_ORDINAL[quarter],
        "value": float(row.value),
        "quarter_end_date": pd.Timestamp(row.ddate_date),
        "available_at": pd.Timestamp(row.accepted_at),
        "derivation": "direct_qtrs_1",
        "source_adshs": str(row.adsh),
        "source_tags": str(row.source_tag),
        "source_forms": str(row.form),
        "source_accepted_ats": pd.Timestamp(row.accepted_at).isoformat(),
    }


def _quarter_states(
    selected: dict[tuple[str, int], pd.Series],
    *,
    concept: str,
    fy: int,
) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}

    q1 = selected.get(("Q1", 1))
    q2_direct = selected.get(("Q2", 1))
    q2_ytd = selected.get(("Q2", 2))
    q3_direct = selected.get(("Q3", 1))
    q3_ytd = selected.get(("Q3", 3))
    annual = selected.get(("FY", 4))

    if q1 is not None:
        states["Q1"] = _direct_state(fy=fy, quarter="Q1", row=q1)

    prefer_ytd = concept in {"revenue", "net_income"}

    if prefer_ytd and q1 is not None and q2_ytd is not None:
        state = _derived_state(
            fy=fy,
            quarter="Q2",
            minuend=q2_ytd,
            subtrahend=q1,
            formula="q2_ytd_minus_q1",
        )
        if state is not None:
            states["Q2"] = state
    elif q2_direct is not None:
        states["Q2"] = _direct_state(
            fy=fy, quarter="Q2", row=q2_direct
        )
    elif q1 is not None and q2_ytd is not None:
        state = _derived_state(
            fy=fy,
            quarter="Q2",
            minuend=q2_ytd,
            subtrahend=q1,
            formula="q2_ytd_minus_q1",
        )
        if state is not None:
            states["Q2"] = state

    if prefer_ytd and q2_ytd is not None and q3_ytd is not None:
        state = _derived_state(
            fy=fy,
            quarter="Q3",
            minuend=q3_ytd,
            subtrahend=q2_ytd,
            formula="q3_ytd_minus_q2_ytd",
        )
        if state is not None:
            states["Q3"] = state
    elif q3_direct is not None:
        states["Q3"] = _direct_state(
            fy=fy, quarter="Q3", row=q3_direct
        )
    elif q2_ytd is not None and q3_ytd is not None:
        state = _derived_state(
            fy=fy,
            quarter="Q3",
            minuend=q3_ytd,
            subtrahend=q2_ytd,
            formula="q3_ytd_minus_q2_ytd",
        )
        if state is not None:
            states["Q3"] = state

    if annual is not None and q3_ytd is not None:
        state = _derived_state(
            fy=fy,
            quarter="Q4",
            minuend=annual,
            subtrahend=q3_ytd,
            formula="annual_minus_q3_ytd",
        )
        if state is not None:
            states["Q4"] = state

    return states


def _state_signature(state: dict[str, object]) -> tuple[object, ...]:
    return (
        state["value"],
        state["quarter_end_date"],
        state["available_at"],
        state["derivation"],
        state["source_adshs"],
        state["source_tags"],
        state["source_forms"],
        state["source_accepted_ats"],
    )


def build_quarter_events(
    duration_winners: pd.DataFrame,
) -> pd.DataFrame:
    """Build versioned YTD-preferred discrete-quarter state changes."""

    required = {
        "cik", "concept", "fy", "fp", "qtrs", "uom", "value",
        "ddate_date", "accepted_at", "adsh", "source_tag", "form",
    }
    missing = sorted(required - set(duration_winners.columns))
    if missing:
        raise ValueError(
            "Duration winners missing historical replay columns: "
            + ", ".join(missing)
        )

    facts = duration_winners.copy().reset_index(drop=True)
    facts["_input_order"] = np.arange(len(facts))
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"], errors="raise"
    )
    facts["ddate_date"] = pd.to_datetime(
        facts["ddate_date"], errors="raise"
    )
    facts["fy"] = pd.to_numeric(facts["fy"], errors="raise").astype(int)
    facts["qtrs"] = pd.to_numeric(
        facts["qtrs"], errors="raise"
    ).astype(int)
    facts["value"] = pd.to_numeric(facts["value"], errors="raise")
    facts["fp"] = facts["fp"].astype(str).str.upper().str.strip()
    facts["uom"] = facts["uom"].astype(str).str.upper().str.strip()
    facts = facts.loc[
        facts["concept"].isin(DURATION_CONCEPTS)
        & facts["fp"].isin({"Q1", "Q2", "Q3", "FY"})
        & facts["qtrs"].isin({1, 2, 3, 4})
    ].copy()

    rows: list[dict[str, object]] = []
    group_keys = ["cik", "concept", "fy", "uom"]

    for key, group in facts.groupby(group_keys, sort=False, dropna=False):
        cik, concept, fy, uom = key
        group = group.sort_values(
            ["accepted_at", "_input_order"], kind="stable"
        )
        selected: dict[tuple[str, int], pd.Series] = {}
        prior: dict[str, tuple[object, ...]] = {}

        for accepted_at, accepted_group in group.groupby(
            "accepted_at", sort=True
        ):
            for _, row in accepted_group.iterrows():
                selected[(str(row.fp), int(row.qtrs))] = row

            states = _quarter_states(
                selected,
                concept=str(concept),
                fy=int(fy),
            )
            for quarter, state in states.items():
                signature = _state_signature(state)
                if prior.get(quarter) == signature:
                    continue
                prior[quarter] = signature
                rows.append(
                    {
                        "cik": int(cik),
                        "concept": str(concept),
                        "uom": str(uom),
                        **state,
                        "effective_at": pd.Timestamp(accepted_at),
                    }
                )

    columns = [
        "cik", "concept", "uom", "fy", "fiscal_quarter",
        "quarter_ordinal", "value", "quarter_end_date", "available_at",
        "derivation", "source_adshs", "source_tags", "source_forms",
        "source_accepted_ats", "effective_at",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["effective_at", "cik", "concept", "uom", "fy", "quarter_ordinal"],
        kind="stable",
    ).reset_index(drop=True)


def _ttm_from_state(
    state: dict[int, dict[str, object]],
    *,
    end_index: int,
    cik: int,
    concept: str,
    uom: str,
    effective_at: pd.Timestamp,
) -> dict[str, object] | None:
    indices = [end_index - 3, end_index - 2, end_index - 1, end_index]
    if not all(index in state for index in indices):
        return None
    quarters = [state[index] for index in indices]
    end_dates = [pd.Timestamp(row["quarter_end_date"]) for row in quarters]
    if not all(left < right for left, right in zip(end_dates, end_dates[1:])):
        return None

    end_fy, end_quarter = _quarter_from_index(end_index)
    return {
        "cik": int(cik),
        "concept": str(concept),
        "uom": str(uom),
        "ttm_end_fy": end_fy,
        "ttm_end_quarter": end_quarter,
        "ttm_end_date": end_dates[-1],
        "ttm_value": float(sum(float(row["value"]) for row in quarters)),
        "available_at": max(
            pd.Timestamp(row["available_at"]) for row in quarters
        ),
        "effective_at": pd.Timestamp(effective_at),
        "quarter_keys": "|".join(
            f"{row['fy']}-{row['fiscal_quarter']}" for row in quarters
        ),
        "quarter_derivations": "|".join(
            str(row["derivation"]) for row in quarters
        ),
        "source_adshs": "||".join(
            str(row["source_adshs"]) for row in quarters
        ),
        "source_tags": "||".join(
            str(row["source_tags"]) for row in quarters
        ),
        "source_forms": "||".join(
            str(row["source_forms"]) for row in quarters
        ),
    }


def build_ttm_events(quarter_events: pd.DataFrame) -> pd.DataFrame:
    """Propagate quarter state changes into versioned four-quarter TTM values."""

    if quarter_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    keys = ["cik", "concept", "uom"]
    for key, group in quarter_events.groupby(keys, sort=False, dropna=False):
        cik, concept, uom = key
        group = group.sort_values(
            ["effective_at", "fy", "quarter_ordinal"], kind="stable"
        )
        state: dict[int, dict[str, object]] = {}
        prior: dict[int, tuple[object, ...]] = {}

        for effective_at, event_group in group.groupby(
            "effective_at", sort=True
        ):
            touched: set[int] = set()
            for _, row in event_group.iterrows():
                index = _quarter_key(int(row.fy), str(row.fiscal_quarter))
                state[index] = row.to_dict()
                touched.add(index)

            endpoints: set[int] = set()
            for index in touched:
                endpoints.update({index, index + 1, index + 2, index + 3})

            for end_index in sorted(endpoints):
                value = _ttm_from_state(
                    state,
                    end_index=end_index,
                    cik=int(cik),
                    concept=str(concept),
                    uom=str(uom),
                    effective_at=pd.Timestamp(effective_at),
                )
                if value is None:
                    continue
                signature = (
                    value["ttm_value"],
                    value["available_at"],
                    value["quarter_keys"],
                    value["quarter_derivations"],
                    value["source_adshs"],
                    value["source_tags"],
                    value["source_forms"],
                )
                if prior.get(end_index) == signature:
                    continue
                prior[end_index] = signature
                rows.append(value)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["effective_at", "cik", "concept", "ttm_end_date", "uom"],
        kind="stable",
    ).reset_index(drop=True)


def replay_ttm_numerators_to_panel(
    panel: pd.DataFrame,
    ttm_events: pd.DataFrame,
) -> pd.DataFrame:
    """Replay TTM events through historical panel decision cutoffs."""

    required_panel = {"decision_date", "as_of", "ticker", "cik"}
    missing = sorted(required_panel - set(panel.columns))
    if missing:
        raise ValueError(
            "Historical panel missing replay columns: " + ", ".join(missing)
        )
    if panel.duplicated(["decision_date", "ticker"]).any():
        raise ValueError("Historical panel has duplicate decision_date/ticker")

    frame = panel[["decision_date", "as_of", "ticker", "cik"]].copy()
    frame["_row_id"] = np.arange(len(frame))
    frame["_cutoff"] = frame["as_of"].map(eastern_timestamp)
    if frame["_cutoff"].isna().any():
        raise ValueError("Historical panel contains invalid as_of cutoffs")

    events = ttm_events.copy()
    events["effective_at"] = pd.to_datetime(
        events["effective_at"], errors="raise"
    ).map(eastern_timestamp)
    events["ttm_end_date"] = pd.to_datetime(
        events["ttm_end_date"], errors="raise"
    )
    events = events.sort_values(
        ["effective_at", "cik", "concept", "ttm_end_date", "uom"],
        kind="stable",
    ).reset_index(drop=True)

    latest: dict[tuple[int, str], dict[str, object]] = {}
    endpoint_state: dict[
        tuple[int, str, str, int, str, pd.Timestamp], dict[str, object]
    ] = {}
    common_cash: dict[int, dict[str, object]] = {}

    event_records = events.to_dict("records")
    position = 0
    output_rows: list[dict[str, object]] = []

    for cutoff, indices in frame.groupby("_cutoff", sort=True).groups.items():
        while position < len(event_records):
            event = event_records[position]
            if event["effective_at"] > cutoff:
                break

            endpoint_key = (
                int(event["cik"]),
                str(event["concept"]),
                str(event["uom"]),
                int(event["ttm_end_fy"]),
                str(event["ttm_end_quarter"]),
                pd.Timestamp(event["ttm_end_date"]),
            )
            endpoint_state[endpoint_key] = event

            simple_key = (int(event["cik"]), str(event["concept"]))
            current = latest.get(simple_key)
            if (
                current is None
                or pd.Timestamp(event["ttm_end_date"])
                > pd.Timestamp(current["ttm_end_date"])
                or (
                    pd.Timestamp(event["ttm_end_date"])
                    == pd.Timestamp(current["ttm_end_date"])
                    and event["effective_at"] >= current["effective_at"]
                )
            ):
                latest[simple_key] = event

            if event["concept"] in {
                "operating_cash_flow", "capital_expenditures"
            }:
                counterpart = (
                    "capital_expenditures"
                    if event["concept"] == "operating_cash_flow"
                    else "operating_cash_flow"
                )
                other_key = (
                    int(event["cik"]),
                    counterpart,
                    str(event["uom"]),
                    int(event["ttm_end_fy"]),
                    str(event["ttm_end_quarter"]),
                    pd.Timestamp(event["ttm_end_date"]),
                )
                other = endpoint_state.get(other_key)
                if other is not None:
                    ocf = (
                        event
                        if event["concept"] == "operating_cash_flow"
                        else other
                    )
                    capex = (
                        event
                        if event["concept"] == "capital_expenditures"
                        else other
                    )
                    candidate = {
                        "ttm_end_date": pd.Timestamp(event["ttm_end_date"]),
                        "available_at": max(
                            pd.Timestamp(ocf["available_at"]),
                            pd.Timestamp(capex["available_at"]),
                        ),
                        "effective_at": max(
                            pd.Timestamp(ocf["effective_at"]),
                            pd.Timestamp(capex["effective_at"]),
                        ),
                        "ttm_operating_cash_flow": float(ocf["ttm_value"]),
                        "ttm_capital_expenditures": float(capex["ttm_value"]),
                        "ttm_free_cash_flow": (
                            float(ocf["ttm_value"])
                            - float(capex["ttm_value"])
                        ),
                    }
                    cash_current = common_cash.get(int(event["cik"]))
                    if (
                        cash_current is None
                        or candidate["ttm_end_date"]
                        > cash_current["ttm_end_date"]
                        or (
                            candidate["ttm_end_date"]
                            == cash_current["ttm_end_date"]
                            and candidate["effective_at"]
                            >= cash_current["effective_at"]
                        )
                    ):
                        common_cash[int(event["cik"])] = candidate
            position += 1

        for index in indices:
            row = frame.loc[index]
            cik = None if pd.isna(row.cik) else int(row.cik)
            revenue = latest.get((cik, "revenue")) if cik is not None else None
            net_income = (
                latest.get((cik, "net_income")) if cik is not None else None
            )
            cash = common_cash.get(cik) if cik is not None else None
            output_rows.append(
                {
                    "_row_id": int(row._row_id),
                    "ttm_revenue": (
                        revenue["ttm_value"] if revenue is not None else np.nan
                    ),
                    "ttm_revenue_available_at": (
                        revenue["available_at"] if revenue is not None else pd.NaT
                    ),
                    "ttm_revenue_end_date": (
                        revenue["ttm_end_date"] if revenue is not None else pd.NaT
                    ),
                    "ttm_net_income": (
                        net_income["ttm_value"]
                        if net_income is not None else np.nan
                    ),
                    "ttm_net_income_available_at": (
                        net_income["available_at"]
                        if net_income is not None else pd.NaT
                    ),
                    "ttm_net_income_end_date": (
                        net_income["ttm_end_date"]
                        if net_income is not None else pd.NaT
                    ),
                    "ttm_operating_cash_flow": (
                        cash["ttm_operating_cash_flow"]
                        if cash is not None else np.nan
                    ),
                    "ttm_capital_expenditures": (
                        cash["ttm_capital_expenditures"]
                        if cash is not None else np.nan
                    ),
                    "ttm_free_cash_flow": (
                        cash["ttm_free_cash_flow"]
                        if cash is not None else np.nan
                    ),
                    "ttm_cash_flow_available_at": (
                        cash["available_at"] if cash is not None else pd.NaT
                    ),
                    "ttm_cash_flow_end_date": (
                        cash["ttm_end_date"] if cash is not None else pd.NaT
                    ),
                }
            )

    replay = pd.DataFrame(output_rows).set_index("_row_id")
    result = frame.set_index("_row_id").join(replay).reset_index(drop=True)
    result = result.drop(columns=["_cutoff"])
    return result.sort_values(
        ["decision_date", "ticker"], kind="stable"
    ).reset_index(drop=True)


def audit_historical_ttm_pit(replay: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    cutoff = replay["as_of"].map(eastern_timestamp)
    for prefix in ("revenue", "net_income", "cash_flow"):
        available_col = f"ttm_{prefix}_available_at"
        if available_col not in replay.columns:
            continue
        available = pd.to_datetime(
            replay[available_col], errors="coerce"
        ).map(eastern_timestamp)
        bad = available.notna() & cutoff.notna() & available.gt(cutoff)
        for index in replay.index[bad]:
            rows.append(
                {
                    "decision_date": replay.at[index, "decision_date"],
                    "ticker": replay.at[index, "ticker"],
                    "field": available_col,
                    "value": replay.at[index, available_col],
                    "status": "available_after_cutoff",
                }
            )
    return pd.DataFrame(
        rows,
        columns=["decision_date", "ticker", "field", "value", "status"],
    )
