"""Research-only SEC availability repair; never mutates the frozen V1 inputs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS
from finance.data.sec_snapshot import ANNUAL_DURATION_CONCEPTS

POLICY = "acceptance_and_filing_day_0600_america_new_york_v1"
PROVENANCE = {
    "ddate_date": "period_date", "period_date": "filing_period_date",
    "filed_date": "filed_date", "accepted_at": "accepted_at",
    "form": "form", "fy": "fy", "fp": "fp", "qtrs": "qtrs",
    "source_tag": "source_tag", "adsh": "adsh",
}


def eastern_timestamp(value: object) -> pd.Timestamp:
    """Legacy naive SEC/panel timestamps are Eastern, never implicitly UTC."""
    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise")
    return stamp.tz_convert("America/New_York")


def audit_panel_availability(frame: pd.DataFrame) -> pd.DataFrame:
    """Check full cutoffs and each populated canonical input, including annuals."""
    rows = []
    def emit(row, field, value, status):
        rows.append(dict(decision_date=row.get("decision_date"), ticker=row.get("ticker"),
                         field=field, value=value, status=status))
    bases = [c for c in CANONICAL_TAGS if c in frame]
    bases += [f"annual_{c}" for c in ANNUAL_DURATION_CONCEPTS if f"annual_{c}" in frame]
    for _, row in frame.iterrows():
        try:
            cutoff = eastern_timestamp(row.get("as_of"))
        except (ValueError, TypeError):
            cutoff = pd.NaT
        if pd.isna(cutoff):
            emit(row, "as_of", row.get("as_of"), "missing_or_invalid_cutoff")
            continue
        for base in bases:
            if pd.isna(row[base]) or str(row[base]).strip() == "":
                continue
            field = base + "_accepted_at"
            try:
                accepted = eastern_timestamp(row.get(field))
            except (ValueError, TypeError):
                accepted = pd.NaT
            if pd.isna(accepted):
                emit(row, field, row.get(field), "missing_or_invalid_acceptance")
            elif accepted > cutoff:
                emit(row, field, row.get(field), "accepted_after_cutoff")
            filed_field = base + "_filed_date"
            try:
                filed = eastern_timestamp(row.get(filed_field))
            except (ValueError, TypeError):
                filed = pd.NaT
            if pd.isna(filed):
                emit(row, filed_field, row.get(filed_field), "missing_or_invalid_filing_date")
            elif filed.normalize() + pd.Timedelta(hours=6) > cutoff:
                emit(row, filed_field, row.get(filed_field), "filing_available_after_cutoff")
    return pd.DataFrame(rows, columns=["decision_date", "ticker", "field", "value", "status"])


class _Cursor:
    def __init__(self, facts, availability):
        self.events = sorted(facts, key=lambda f: (f[availability], f["cik"], f["concept"], f["ddate_date"]))
        self.availability = availability
        self.position = 0
        self.latest = {}

    def advance(self, cutoff):
        while self.position < len(self.events):
            fact = self.events[self.position]
            if fact[self.availability] > cutoff:
                break
            key = (fact["cik"], fact["concept"])
            rank = (fact["ddate_date"], fact["accepted_at"])
            current = self.latest.get(key)
            if current is None or rank >= (current["ddate_date"], current["accepted_at"]):
                self.latest[key] = fact
            self.position += 1


def _same(left, right, suffix):
    if pd.isna(left) or str(left).strip() == "":
        return pd.isna(right) or str(right).strip() == ""
    if pd.isna(right):
        return False
    if suffix == "accepted_at":
        return eastern_timestamp(left) == eastern_timestamp(right)
    if suffix.endswith("date"):
        return pd.Timestamp(left).date() == pd.Timestamp(right).date()
    if suffix in ("value", "fy", "qtrs"):
        return bool(np.isclose(float(left), float(right), rtol=1e-12, atol=0))
    return str(left) == str(right)


def reconcile_panel(panel: pd.DataFrame, winners: pd.DataFrame, *, progress=None):
    """Replay original selections, then rebuild all SEC inputs using availability.

    Requires the exact baseline winner cache. Any original value/provenance
    mismatch aborts. Membership, identity, prices, and V1 model rules are retained.
    Filing-day 06:00 Eastern is a conservative eligibility bound for this weekly
    financial-statement research, not a claimed exact public dissemination time.
    """
    required = {"cik", "concept", "value", "accepted_at", "filed_date", "ddate_date",
                "period_date", "adsh", "source_tag", "form", "qtrs"}
    if required - set(winners):
        raise ValueError(f"Winner cache missing columns: {sorted(required - set(winners))}")
    if {"as_of", "decision_date", "ticker", "cik"} - set(panel):
        raise ValueError("Panel requires as_of, decision_date, ticker, cik")
    if panel.duplicated(["decision_date", "ticker"]).any():
        raise ValueError("Duplicate decision_date/ticker in historical panel")
    facts = winners.copy()
    facts["accepted_at"] = facts["accepted_at"].map(eastern_timestamp)
    facts["filed_date"] = pd.to_datetime(facts["filed_date"], errors="raise").dt.date
    for col in ("ddate_date", "period_date"):
        facts[col] = pd.to_datetime(facts[col], errors="raise").dt.date
    if facts[list(required)].isna().any().any():
        raise ValueError("Winner cache has missing required fact/provenance values")
    if not facts["concept"].isin(CANONICAL_TAGS).all():
        raise ValueError("Winner cache has unsupported concepts")
    facts["cik"] = pd.to_numeric(facts["cik"], errors="raise").astype(int)
    facts["qtrs"] = pd.to_numeric(facts["qtrs"], errors="raise")
    # The baseline historical SEC winner cache can contain multiple rows for
    # the same CIK/concept/period/acceptance timestamp. The original
    # SecWinnerFactCursor resolved equal-rank ties by stable cache order: the
    # later row encountered replaced the earlier row. Preserve that historical
    # behavior here instead of inventing a new value/tag tie-breaker. The
    # original replay comparison below remains the fail-closed proof that this
    # ordering reproduces the frozen historical panel.
    facts["_available_at"] = [max(a, eastern_timestamp(f) + pd.Timedelta(hours=6))
                              for a, f in zip(facts.accepted_at, facts.filed_date)]
    records = facts.to_dict("records")
    annual = [f for f in records if f["concept"] in ANNUAL_DURATION_CONCEPTS and f["qtrs"] == 4]
    cursors = {key: _Cursor(rows, availability) for key, rows, availability in (
        ("old", records, "accepted_at"), ("new", records, "_available_at"),
        ("old_annual", annual, "accepted_at"), ("new_annual", annual, "_available_at"))}
    output = panel.copy().reset_index(drop=True)
    cutoffs = panel["as_of"].map(eastern_timestamp).reset_index(drop=True)
    if cutoffs.isna().any():
        raise ValueError("Missing decision cutoff")
    dates = pd.to_datetime(panel["decision_date"], errors="raise").dt.date.reset_index(drop=True)
    if any(c.date() != d for c, d in zip(cutoffs, dates)):
        raise ValueError("decision_date differs from Eastern as_of date")
    bases = [(c, c, False) for c in CANONICAL_TAGS if c in panel]
    bases += [(f"annual_{c}", c, True) for c in sorted(ANNUAL_DURATION_CONCEPTS) if f"annual_{c}" in panel]
    # Annual snapshots originally omitted filing/accession metadata. Restore it
    # from the verified winner for the stricter availability audit.
    for base, _, is_annual in bases:
        if is_annual:
            for suffix in ("filed_date", "adsh"):
                if base + "_" + suffix not in output:
                    output[base + "_" + suffix] = pd.Series(pd.NA, index=output.index, dtype="object")
    changes = []
    groups = cutoffs.groupby(cutoffs, sort=True).groups
    for step, (cutoff, indices) in enumerate(groups.items(), start=1):
        for cursor in cursors.values():
            cursor.advance(cutoff)
        for index in indices:
            row = panel.iloc[index]
            cik = None if pd.isna(row.cik) else int(row.cik)
            for base, concept, is_annual in bases:
                suffix = "_annual" if is_annual else ""
                old = cursors["old" + suffix].latest.get((cik, concept), {})
                new = cursors["new" + suffix].latest.get((cik, concept), {})
                columns = {base: "value"}
                columns.update({base + "_" + dest: source for source, dest in PROVENANCE.items()
                                if base + "_" + dest in output})
                for col, source in columns.items():
                    comparison_type = "value" if col == base else col[len(base)+1:]
                    previous = row.get(col, np.nan)
                    if col in panel and not _same(previous, old.get(source, np.nan), comparison_type):
                        raise ValueError(f"Original replay mismatch: {row.decision_date} {row.ticker} {col}; verify baseline winner cache")
                    value = new.get(source, np.nan)
                    if source == "accepted_at" and pd.notna(value):
                        value = value.tz_localize(None).isoformat(sep=" ")
                    if col not in panel or not _same(previous, value, comparison_type):
                        changes.append(dict(decision_date=row.decision_date, ticker=row.ticker,
                                            field=col, before=previous, after=value,
                                            change_type="provenance_added" if col not in panel else "availability_reselection"))
                        # Preserve mixed string/date columns across pandas versions.
                        if output[col].dtype != object and comparison_type != "value":
                            output[col] = output[col].astype(object)
                        output.at[index, col] = value
        if progress is not None and (step == 1 or step % 25 == 0 or step == len(groups)):
            progress(f"PIT replay {step}/{len(groups)} dates; through {cutoff.date()}")
    fundamental_cols = [c for c in CANONICAL_TAGS if c in output]
    if "fundamentals_available" in output:
        output["fundamentals_available"] = output[fundamental_cols].notna().any(axis=1)
    if "research_ready" in output:
        def boolean(s):
            return s.astype(str).str.lower().isin(["true", "1"])
        output["research_ready"] = (boolean(output.identity_resolved) & boolean(output.price_available)
                                    & output.fundamentals_available)
    audit = audit_panel_availability(output)
    if not audit.empty:
        raise ValueError(f"Reconciled panel still has {len(audit)} availability violations")
    detail = pd.DataFrame(changes, columns=["decision_date", "ticker", "field", "before", "after", "change_type"])
    return output, detail, audit
