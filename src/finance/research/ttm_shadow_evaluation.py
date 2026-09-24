from __future__ import annotations

from collections import Counter

import pandas as pd


FAMILY_COLUMNS = {
    "quality": "quality_score",
    "financial_health": "financial_health_score",
    "growth": "growth_score",
    "annual_valuation": "valuation_score",
    "ttm_valuation": "ttm_valuation_score",
}


def _count_present(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(pd.to_numeric(frame[column], errors="coerce").notna().sum())


def _count_true(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(frame[column].fillna(False).astype(bool).sum())


def weekly_coverage(frame: pd.DataFrame) -> dict[str, int]:
    """Return deterministic current-family coverage counts for one shadow week."""

    result = {
        f"{name}_eligible": _count_present(frame, column)
        for name, column in FAMILY_COLUMNS.items()
    }
    result["v1_top_conviction_eligible"] = _count_true(
        frame, "top_conviction_eligible"
    )
    result["v2_top_conviction_eligible"] = _count_true(
        frame, "v2_top_conviction_eligible"
    )
    result["universe_rows"] = int(len(frame))
    return result


def _split_tickers(value: object) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return [
        part.strip().upper()
        for part in str(value).split("|")
        if part.strip()
    ]


def _series_stat(values: pd.Series, method: str) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    if method == "mean":
        return float(numeric.mean())
    if method == "median":
        return float(numeric.median())
    if method == "min":
        return float(numeric.min())
    if method == "max":
        return float(numeric.max())
    raise ValueError(f"Unsupported statistic: {method}")


def build_period_summary(
    ledger: pd.DataFrame,
    weekly_detail: pd.DataFrame,
    ticker_events: pd.DataFrame,
) -> dict[str, object]:
    """Aggregate only predeclared Issue #24 shadow-evaluation fields."""

    valid = (
        ledger["valid"].fillna(False).astype(bool)
        if "valid" in ledger.columns
        else pd.Series(False, index=ledger.index)
    )
    valid_ledger = ledger.loc[valid].copy()

    entered = Counter()
    exited = Counter()
    for value in valid_ledger.get("entered_v2", pd.Series(dtype="object")):
        entered.update(_split_tickers(value))
    for value in valid_ledger.get("exited_v2", pd.Series(dtype="object")):
        exited.update(_split_tickers(value))

    persistent = sorted(
        ticker
        for ticker in set(entered) | set(exited)
        if entered[ticker] + exited[ticker] >= 2
    )

    summary: dict[str, object] = {
        "schema_version": 1,
        "evaluation_protocol": "issue24_shadow_evaluation_v1",
        "valid_shadow_weeks": int(len(valid_ledger)),
        "recorded_ledger_rows": int(len(ledger)),
        "excluded_or_invalid_weeks_in_ledger": int(len(ledger) - len(valid_ledger)),
        "first_valid_week": (
            str(valid_ledger["as_of"].astype(str).min())
            if not valid_ledger.empty
            else None
        ),
        "latest_valid_week": (
            str(valid_ledger["as_of"].astype(str).max())
            if not valid_ledger.empty
            else None
        ),
        "top10_overlap_mean": _series_stat(
            valid_ledger.get("top10_overlap", pd.Series(dtype=float)), "mean"
        ),
        "top10_overlap_median": _series_stat(
            valid_ledger.get("top10_overlap", pd.Series(dtype=float)), "median"
        ),
        "top10_overlap_min": _series_stat(
            valid_ledger.get("top10_overlap", pd.Series(dtype=float)), "min"
        ),
        "entered_v2_frequency": dict(sorted(entered.items())),
        "exited_v2_frequency": dict(sorted(exited.items())),
        "persistent_difference_tickers": persistent,
        "pit_violation_count": int(
            pd.to_numeric(
                valid_ledger.get("pit_violations", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()
        ),
        "unique_v1_decision_hashes": int(
            valid_ledger.get(
                "v1_decision_hash", pd.Series(dtype="object")
            ).astype(str).nunique()
        ),
        "unique_v2_decision_hashes": int(
            valid_ledger.get(
                "v2_decision_hash", pd.Series(dtype="object")
            ).astype(str).nunique()
        ),
        "unique_input_bundles": int(
            valid_ledger.get(
                "input_bundle_sha256", pd.Series(dtype="object")
            ).astype(str).nunique()
        ),
        "unique_code_commits": int(
            valid_ledger.get(
                "code_commit", pd.Series(dtype="object")
            ).astype(str).nunique()
        ),
        "weekly_artifact_consistency_failures": int(
            (~weekly_detail["artifact_consistent"].fillna(False).astype(bool)).sum()
            if not weekly_detail.empty and "artifact_consistent" in weekly_detail
            else 0
        ),
        "manual_intervention": {
            "status": "not_automatically_inferable",
            "note": (
                "Manual intervention before or outside the successful shadow "
                "command is not inferable from existing artifacts and must be "
                "recorded explicitly during final review if it occurs."
            ),
        },
        "failed_attempts": {
            "status": "not_recorded_in_success_ledger",
            "note": (
                "The current #18 ledger contains successful observations only. "
                "Any failed/excluded attempted week must be preserved separately "
                "and documented in the final review."
            ),
        },
        "ticker_event_rows": int(len(ticker_events)),
        "live_promotion_authorized": False,
    }

    if not weekly_detail.empty:
        coverage_columns = [
            column
            for column in weekly_detail.columns
            if column.endswith("_eligible") or column == "universe_rows"
        ]
        ranges = {}
        for column in coverage_columns:
            numeric = pd.to_numeric(weekly_detail[column], errors="coerce").dropna()
            if not numeric.empty:
                ranges[column] = {
                    "min": int(numeric.min()),
                    "max": int(numeric.max()),
                }
        summary["coverage_ranges"] = ranges
    else:
        summary["coverage_ranges"] = {}

    return summary


def build_ticker_event_detail(
    comparisons: list[tuple[str, pd.DataFrame]],
) -> pd.DataFrame:
    """Normalize weekly entered/exited/overlap evidence for final review."""

    rows: list[dict[str, object]] = []
    for as_of, frame in comparisons:
        if frame.empty:
            continue
        for row in frame.itertuples(index=False):
            rows.append(
                {
                    "as_of": as_of,
                    "ticker": str(row.ticker),
                    "change": str(row.change),
                    "v1_rank": getattr(row, "v1_rank", None),
                    "v2_rank": getattr(row, "v2_rank", None),
                    "rank_change_v2_minus_v1": getattr(
                        row, "rank_change_v2_minus_v1", None
                    ),
                }
            )
    return pd.DataFrame(rows)


def render_evaluation_markdown(
    summary: dict[str, object],
    weekly_detail: pd.DataFrame,
) -> str:
    """Render the frozen Issue #24 evaluation template with current evidence."""

    overlap_mean = summary.get("top10_overlap_mean")
    overlap_median = summary.get("top10_overlap_median")
    overlap_min = summary.get("top10_overlap_min")

    def fmt(value: object) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    lines = [
        "# V2 TTM Shadow Evaluation",
        "",
        "Evaluation protocol: issue24_shadow_evaluation_v1",
        "",
        "This template was defined before the completed shadow-period evidence "
        "was available. It summarizes evidence; it does not authorize live V2.",
        "",
        "## Observation validity",
        "",
        f"- Valid shadow weeks: {summary['valid_shadow_weeks']}",
        f"- Recorded ledger rows: {summary['recorded_ledger_rows']}",
        (
            "- Invalid/excluded rows recorded in success ledger: "
            f"{summary['excluded_or_invalid_weeks_in_ledger']}"
        ),
        f"- PIT violations: {summary['pit_violation_count']}",
        (
            "- Weekly artifact-consistency failures: "
            f"{summary['weekly_artifact_consistency_failures']}"
        ),
        "",
        "## Decision stability",
        "",
        f"- Mean V1/V2 Top-10 overlap: {fmt(overlap_mean)}/10",
        f"- Median V1/V2 Top-10 overlap: {fmt(overlap_median)}/10",
        f"- Minimum V1/V2 Top-10 overlap: {fmt(overlap_min)}/10",
        (
            "- Persistent V1/V2 difference tickers (2+ entry/exit events): "
            + (
                ", ".join(summary["persistent_difference_tickers"])
                if summary["persistent_difference_tickers"]
                else "none"
            )
        ),
        "",
        "## Coverage stability",
        "",
    ]
    ranges = summary.get("coverage_ranges", {})
    if ranges:
        for field, values in sorted(ranges.items()):
            lines.append(
                f"- {field}: {values['min']} to {values['max']} rows"
            )
    else:
        lines.append("- No valid weekly coverage evidence yet.")

    lines.extend([
        "",
        "## Provenance and reproducibility",
        "",
        f"- Unique V1 decision hashes: {summary['unique_v1_decision_hashes']}",
        f"- Unique V2 decision hashes: {summary['unique_v2_decision_hashes']}",
        f"- Unique input bundles: {summary['unique_input_bundles']}",
        f"- Code commits observed: {summary['unique_code_commits']}",
        (
            "- Existing artifacts support hash/provenance consistency checks. "
            "A full rerun replay is not automatically performed by this summary."
        ),
        "",
        "## Evidence gaps requiring explicit final-review notation",
        "",
        "- Failed attempted weeks are not written into the successful #18 ledger.",
        "- Manual intervention outside the successful shadow command cannot be "
        "inferred automatically from current artifacts.",
        "",
        "## Final disposition (complete after required shadow period)",
        "",
        "- [ ] At least 8 valid weekly observations completed.",
        "- [ ] Failed/excluded attempts, if any, documented.",
        "- [ ] Manual interventions, if any, documented.",
        "- [ ] Weekly Top-10 differences and persistence reviewed.",
        "- [ ] Coverage stability reviewed.",
        "- [ ] PIT/fingerprint/artifact consistency reviewed.",
        "- [ ] Shadow evidence compared with historical challenger behavior.",
        "- [ ] Remaining operational/data limitations documented.",
        "",
        "**No automatic promotion:** satisfying this template only makes the "
        "challenger eligible for a separate explicit live-integration decision.",
        "",
    ])

    if not weekly_detail.empty:
        lines.extend([
            "## Weekly evidence table",
            "",
            weekly_detail.to_markdown(index=False),
            "",
        ])

    return "\n".join(lines)
