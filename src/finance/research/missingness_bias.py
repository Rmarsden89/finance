from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd


FAMILIES = ("quality", "financial_health", "growth", "valuation")
INPUT_METRICS = (
    "return_price",
    "total_assets",
    "total_liabilities",
    "cash_and_equivalents",
    "operating_cash_flow",
    "shares_outstanding",
    "market_cap",
)
MARKET_CAP_BINS = [-np.inf, 2e9, 10e9, 50e9, 200e9, np.inf]
MARKET_CAP_LABELS = ["<2B", "2B-10B", "10B-50B", "50B-200B", ">=200B"]


PROMOTION_THRESHOLDS = {
    "schema_version": 1,
    "coverage_improvement_min_percentage_points": 5.0,
    "current_top10_overlap_min": 8,
    "median_absolute_rank_displacement_max": 10.0,
    "mean_weekly_replacement_rate_increase_max_percentage_points": 5.0,
    "unexplained_replacement_rate_increase_max_percentage_points": 10.0,
    "top10_market_cap_band_share_increase_max_percentage_points": 10.0,
    "pit_violations_required": 0,
    "performance_min_nonoverlapping_weekly_observations": 52,
    "performance_use": "diagnostic_until_frozen_out_of_sample_evaluation",
    "imputation_allowed": False,
}


@dataclass(frozen=True)
class MissingnessBiasSummary:
    universe_rows: int
    decision_dates: int
    current_rows: int
    current_full_four_family: int
    current_three_family: int
    current_fewer_than_three: int
    current_return_price_present: int
    current_shares_coverage_gain_percentage_points: float
    current_valuation_coverage_gain_percentage_points: float
    current_top10_overlap: int
    current_median_absolute_rank_displacement: float | None
    mean_replacement_rate_delta_percentage_points: float | None
    max_weekly_replacement_rate_delta_percentage_points: float | None
    max_market_cap_band_share_increase_percentage_points: float | None
    availability_gain_rows: int
    availability_loss_rows: int
    score_only_change_rows: int
    classification_metadata_available: bool
    one_week_forward_decision_dates: int
    full_four_family_forward_observations: int
    populated_top10_comparison_dates: int
    comparable_turnover_transitions: int
    historical_top10_analysis_status: str
    forward_return_analysis_status: str
    point_in_time_violations: int


def _bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna("").astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _input_present(frame: pd.DataFrame, column: str) -> pd.Series:
    values = _numeric(frame, column)
    if column in {
        "return_price", "total_assets", "total_liabilities",
        "shares_outstanding", "market_cap",
    }:
        return values.gt(0)
    return values.notna()


def _require(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def prepare_missingness_panel(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a scored panel and add explicit family/missingness cohorts."""

    required = ["decision_date", "ticker", "long_growth_v1_score"]
    required += [f"{family}_score" for family in FAMILIES]
    _require(frame, required, "scored panel")
    result = frame.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"], errors="coerce"
    ).dt.normalize()
    result["ticker"] = result["ticker"].astype(str).str.upper()
    if result["decision_date"].isna().any():
        raise ValueError("scored panel has invalid decision_date rows")
    if result["ticker"].str.strip().isin({"", "NAN", "NONE"}).any():
        raise ValueError("scored panel has blank ticker rows")
    if result[["decision_date", "ticker"]].duplicated().any():
        raise ValueError("scored panel has duplicate decision_date/ticker rows")

    available_columns: list[str] = []
    for family in FAMILIES:
        score = _numeric(result, f"{family}_score")
        column = f"_{family}_available"
        result[column] = score.notna()
        available_columns.append(column)
    result["family_count"] = result[available_columns].sum(axis=1).astype(int)
    result["family_cohort"] = np.select(
        [result["family_count"].eq(4), result["family_count"].eq(3)],
        ["full_four_family", "three_family"],
        default="fewer_than_three",
    )
    result["missing_families"] = result.apply(
        lambda row: "|".join(
            family
            for family in FAMILIES
            if not bool(row[f"_{family}_available"])
        ) or "none",
        axis=1,
    )
    result["market_cap_band"] = pd.cut(
        _numeric(result, "market_cap"),
        bins=MARKET_CAP_BINS,
        labels=MARKET_CAP_LABELS,
        right=False,
    ).astype("object")
    result["market_cap_band"] = result["market_cap_band"].fillna("missing")
    if "top_conviction_eligible" in result:
        result["_top_eligible"] = _bool(result["top_conviction_eligible"])
    else:
        result["_top_eligible"] = result["family_count"].eq(4)
    if "long_growth_v1_eligible" in result:
        result["_model_eligible"] = _bool(result["long_growth_v1_eligible"])
    else:
        result["_model_eligible"] = result["family_count"].ge(3)
    return result


def coverage_table(frame: pd.DataFrame, *, current_only: bool = False) -> pd.DataFrame:
    """Return long-form family and input coverage by decision year."""

    panel = prepare_missingness_panel(frame)
    if current_only:
        panel = panel.loc[panel["decision_date"].eq(panel["decision_date"].max())]
    panel = panel.assign(year=panel["decision_date"].dt.year)
    metrics: dict[str, pd.Series] = {
        **{
            f"family:{family}": panel[f"_{family}_available"]
            for family in FAMILIES
        },
        **{
            f"input:{column}": _input_present(panel, column)
            for column in INPUT_METRICS
            if column in panel
        },
        "eligibility:three_or_more_families": panel["family_count"].ge(3),
        "eligibility:full_four_families": panel["family_count"].eq(4),
    }
    rows: list[dict[str, object]] = []
    for year, indices in panel.groupby("year", dropna=False).groups.items():
        for metric, available in metrics.items():
            present = int(available.loc[indices].sum())
            total = len(indices)
            rows.append(
                {
                    "year": year,
                    "metric": metric,
                    "rows": total,
                    "present": present,
                    "coverage_pct": present / total if total else np.nan,
                }
            )
    return pd.DataFrame(rows)


def cohort_counts(frame: pd.DataFrame) -> pd.DataFrame:
    panel = prepare_missingness_panel(frame).assign(
        year=lambda value: value["decision_date"].dt.year
    )
    return (
        panel.groupby(
            ["year", "family_cohort", "missing_families"],
            dropna=False,
            as_index=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["year", "family_cohort", "rows", "missing_families"],
            ascending=[True, True, False, True],
            kind="stable",
        )
    )


def current_cohorts_by_group(
    frame: pd.DataFrame, *, candidates: tuple[str, ...]
) -> pd.DataFrame:
    panel = prepare_missingness_panel(frame)
    current = panel.loc[panel["decision_date"].eq(panel["decision_date"].max())].copy()
    groupings = [column for column in candidates if column in current]
    if not groupings:
        return pd.DataFrame(
            columns=["grouping", "group", "family_cohort", "rows", "row_share"]
        )
    outputs: list[pd.DataFrame] = []
    for grouping in groupings:
        grouped = current.assign(
            _group=current[grouping].fillna("(missing)").astype(str)
        )
        result = (
            grouped.groupby(["_group", "family_cohort"], as_index=False)
            .size()
            .rename(columns={"_group": "group", "size": "rows"})
        )
        totals = result.groupby("group")["rows"].transform("sum")
        result["row_share"] = result["rows"] / totals
        result.insert(0, "grouping", grouping)
        outputs.append(result)
    return pd.concat(outputs, ignore_index=True)


def add_selection_rank(frame: pd.DataFrame) -> pd.DataFrame:
    panel = prepare_missingness_panel(frame)
    panel["selection_rank"] = np.nan
    panel["top10"] = False
    panel["model_rank"] = np.nan
    panel["model_top10"] = False
    for _, group in panel.groupby("decision_date", sort=True):
        model_eligible = group.loc[
            group["_model_eligible"]
            & _numeric(group, "long_growth_v1_score").notna()
        ].sort_values(
            ["long_growth_v1_score", "ticker"],
            ascending=[False, True],
            kind="stable",
        )
        panel.loc[model_eligible.index, "model_rank"] = range(
            1, len(model_eligible) + 1
        )
        panel.loc[model_eligible.head(10).index, "model_top10"] = True
        eligible = group.loc[
            group["_top_eligible"]
            & _numeric(group, "long_growth_v1_score").notna()
        ].sort_values(
            ["long_growth_v1_score", "ticker"],
            ascending=[False, True],
            kind="stable",
        )
        panel.loc[eligible.index, "selection_rank"] = range(1, len(eligible) + 1)
        panel.loc[eligible.head(10).index, "top10"] = True
    return panel


def forward_return_analysis(
    frame: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 4, 13),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Measure later returns using the cohort known on each start date."""

    panel = add_selection_rank(frame).sort_values(
        ["ticker", "decision_date"], kind="stable"
    )
    panel["return_price"] = _numeric(panel, "return_price")
    groups = panel.groupby("ticker", sort=False)
    details: list[pd.DataFrame] = []
    for horizon in horizons:
        future_date = groups["decision_date"].shift(-horizon)
        future_price = groups["return_price"].shift(-horizon)
        future_full = groups["family_count"].shift(-horizon).eq(4)
        future_eligible = groups["_top_eligible"].shift(-horizon).eq(True)
        future_top10 = groups["top10"].shift(-horizon).eq(True)
        future_rank = groups["selection_rank"].shift(-horizon)
        future_model_rank = groups["model_rank"].shift(-horizon)
        future_model_top10 = groups["model_top10"].shift(-horizon).eq(True)
        day_gap = (future_date - panel["decision_date"]).dt.days
        windows = {1: (4, 10), 4: (21, 42), 13: (70, 105)}
        minimum_days, maximum_days = windows.get(
            horizon, (5 * horizon, 10 * horizon + 2)
        )
        valid = (
            panel["return_price"].gt(0)
            & future_price.gt(0)
            & day_gap.between(minimum_days, maximum_days)
        )
        detail = panel.loc[
            valid,
            [
                "decision_date",
                "ticker",
                "family_cohort",
                "missing_families",
                "family_count",
                "selection_rank",
                "top10",
                "model_rank",
                "model_top10",
            ],
        ].copy()
        detail["horizon_weeks"] = horizon
        detail["future_decision_date"] = future_date.loc[valid]
        detail["day_gap"] = day_gap.loc[valid]
        detail["forward_return"] = (
            future_price.loc[valid] / panel.loc[valid, "return_price"] - 1.0
        )
        detail["full_four_family_at_horizon"] = future_full.loc[valid]
        detail["top_conviction_eligible_at_horizon"] = future_eligible.loc[valid]
        detail["top10_at_horizon"] = future_top10.loc[valid]
        detail["selection_rank_at_horizon"] = future_rank.loc[valid]
        detail["model_rank_at_horizon"] = future_model_rank.loc[valid]
        detail["model_top10_at_horizon"] = future_model_top10.loc[valid]
        details.append(detail)
    columns = [
        "decision_date", "ticker", "family_cohort", "missing_families",
        "family_count", "selection_rank", "top10", "model_rank",
        "model_top10", "horizon_weeks",
        "future_decision_date", "day_gap", "forward_return",
        "full_four_family_at_horizon", "top_conviction_eligible_at_horizon",
        "top10_at_horizon", "selection_rank_at_horizon",
        "model_rank_at_horizon", "model_top10_at_horizon",
    ]
    detail = pd.concat(details, ignore_index=True) if details else pd.DataFrame(columns=columns)
    if detail.empty:
        return detail, pd.DataFrame(
            columns=[
                "horizon_weeks", "family_cohort", "missing_families",
                "observations", "decision_dates", "mean_forward_return",
                "median_forward_return", "positive_return_rate",
                "full_four_family_at_horizon_rate",
                "top_conviction_eligible_at_horizon_rate",
                "top10_at_horizon_rate", "median_selection_rank_at_horizon",
                "model_top10_at_horizon_rate",
                "median_model_rank_at_horizon",
            ]
        )
    summary = (
        detail.groupby(
            ["horizon_weeks", "family_cohort", "missing_families"],
            as_index=False,
            dropna=False,
        )
        .agg(
            observations=("ticker", "size"),
            decision_dates=("decision_date", "nunique"),
            mean_forward_return=("forward_return", "mean"),
            median_forward_return=("forward_return", "median"),
            positive_return_rate=("forward_return", lambda value: value.gt(0).mean()),
            full_four_family_at_horizon_rate=(
                "full_four_family_at_horizon", "mean"
            ),
            top_conviction_eligible_at_horizon_rate=(
                "top_conviction_eligible_at_horizon", "mean"
            ),
            top10_at_horizon_rate=("top10_at_horizon", "mean"),
            median_selection_rank_at_horizon=(
                "selection_rank_at_horizon", "median"
            ),
            model_top10_at_horizon_rate=("model_top10_at_horizon", "mean"),
            median_model_rank_at_horizon=("model_rank_at_horizon", "median"),
        )
        .sort_values(
            ["horizon_weeks", "family_cohort", "observations"],
            ascending=[True, True, False],
            kind="stable",
        )
    )
    return detail, summary


def historical_top10_analysis(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build populated historical Top-10, turnover, and concentration evidence."""

    ranked = add_selection_rank(frame)
    weekly_rows: list[dict[str, object]] = []
    top10_by_date: dict[pd.Timestamp, list[str]] = {}
    for decision_date, group in ranked.groupby("decision_date", sort=True):
        selected = group.loc[group["top10"]].sort_values("selection_rank")
        tickers = selected["ticker"].tolist()
        top10_by_date[decision_date] = tickers
        weekly_rows.append(
            {
                "decision_date": decision_date,
                "top10_count": len(tickers),
                "top10_populated": len(tickers) == 10,
                "top10_tickers": "|".join(tickers),
            }
        )
    weekly = pd.DataFrame(weekly_rows)

    turnover_rows: list[dict[str, object]] = []
    dates = sorted(top10_by_date)
    for position in range(1, len(dates)):
        prior_date = dates[position - 1]
        decision_date = dates[position]
        prior = top10_by_date[prior_date]
        current = top10_by_date[decision_date]
        day_gap = (decision_date - prior_date).days
        valid = len(prior) == 10 and len(current) == 10 and 4 <= day_gap <= 10
        prior_set = set(prior)
        current_set = set(current)
        turnover_rows.append(
            {
                "decision_date": decision_date,
                "prior_decision_date": prior_date,
                "day_gap": day_gap,
                "prior_top10_count": len(prior),
                "top10_count": len(current),
                "overlap_count": len(prior_set & current_set),
                "entrants_count": len(current_set - prior_set),
                "replacement_rate": (
                    len(current_set - prior_set) / 10 if valid else np.nan
                ),
                "comparison_valid": valid,
            }
        )
    turnover = pd.DataFrame(turnover_rows)

    concentration_rows: list[dict[str, object]] = []
    for decision_date, group in ranked.groupby("decision_date", sort=True):
        selected = group.loc[group["top10"]]
        if len(selected) != 10:
            continue
        counts = selected["market_cap_band"].value_counts(dropna=False)
        for band in [*MARKET_CAP_LABELS, "missing"]:
            count = int(counts.get(band, 0))
            concentration_rows.append(
                {
                    "decision_date": decision_date,
                    "market_cap_band": band,
                    "band_count": count,
                    "band_share": count / 10,
                }
            )
    concentration = pd.DataFrame(concentration_rows)
    return weekly, turnover, concentration


def historical_cohorts_by_group(
    frame: pd.DataFrame,
    *,
    candidates: tuple[str, ...],
) -> pd.DataFrame:
    """Summarize historical missingness cohorts by available classifications."""

    panel = prepare_missingness_panel(frame)
    panel["year"] = panel["decision_date"].dt.year
    groupings = [column for column in candidates if column in panel]
    if not groupings:
        return pd.DataFrame(
            columns=[
                "year", "grouping", "group", "family_cohort", "rows",
                "group_rows", "cohort_share_within_group",
            ]
        )
    outputs: list[pd.DataFrame] = []
    for grouping in groupings:
        grouped = panel.assign(
            _group=panel[grouping].fillna("(missing)").astype(str)
        )
        result = (
            grouped.groupby(
                ["year", "_group", "family_cohort"], as_index=False
            )
            .size()
            .rename(columns={"_group": "group", "size": "rows"})
        )
        result["group_rows"] = result.groupby(
            ["year", "group"]
        )["rows"].transform("sum")
        result["cohort_share_within_group"] = (
            result["rows"] / result["group_rows"]
        )
        result.insert(1, "grouping", grouping)
        outputs.append(result)
    return pd.concat(outputs, ignore_index=True)


def current_rank_shift_diagnostic(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Explain current rank changes by baseline rank band and changed family."""

    left = add_selection_rank(baseline)
    right = add_selection_rank(challenger)
    current_date = min(left["decision_date"].max(), right["decision_date"].max())
    columns = [
        "decision_date", "ticker", "family_count", "_top_eligible",
        "long_growth_v1_score", "selection_rank", *(
            f"{family}_score" for family in FAMILIES
        ),
    ]
    detail = left.loc[left["decision_date"].eq(current_date), columns].merge(
        right.loc[right["decision_date"].eq(current_date), columns],
        on=["decision_date", "ticker"],
        how="outer",
        suffixes=("_baseline", "_challenger"),
        validate="one_to_one",
    )
    for side in ("baseline", "challenger"):
        detail[f"_top_eligible_{side}"] = _bool(
            detail[f"_top_eligible_{side}"]
        )
    detail["continuously_eligible"] = (
        detail["_top_eligible_baseline"]
        & detail["_top_eligible_challenger"]
    )
    detail["rank_change"] = (
        _numeric(detail, "selection_rank_challenger")
        - _numeric(detail, "selection_rank_baseline")
    )
    detail["absolute_rank_change"] = detail["rank_change"].abs()
    detail["composite_score_change"] = (
        _numeric(detail, "long_growth_v1_score_challenger")
        - _numeric(detail, "long_growth_v1_score_baseline")
    )
    delta_columns: list[str] = []
    for family in FAMILIES:
        column = f"{family}_score_change"
        detail[column] = (
            _numeric(detail, f"{family}_score_challenger")
            - _numeric(detail, f"{family}_score_baseline")
        )
        delta_columns.append(column)

    absolute_deltas = detail[delta_columns].abs()
    detail["dominant_changed_family"] = absolute_deltas.idxmax(axis=1).str.replace(
        "_score_change", "", regex=False
    )
    detail.loc[absolute_deltas.max(axis=1).fillna(0).le(1e-12), "dominant_changed_family"] = "unchanged"
    baseline_rank = _numeric(detail, "selection_rank_baseline")
    detail["baseline_rank_band"] = pd.cut(
        baseline_rank,
        bins=[0, 10, 25, 50, 100, 200, np.inf],
        labels=["1-10", "11-25", "26-50", "51-100", "101-200", "201+"],
    ).astype("object").fillna("not_ranked")

    eligible = detail.loc[detail["continuously_eligible"]].copy()
    by_band = (
        eligible.groupby("baseline_rank_band", as_index=False, dropna=False)
        .agg(
            rows=("ticker", "size"),
            median_absolute_rank_change=("absolute_rank_change", "median"),
            mean_absolute_rank_change=("absolute_rank_change", "mean"),
            maximum_absolute_rank_change=("absolute_rank_change", "max"),
            moved_more_than_10=("absolute_rank_change", lambda value: value.gt(10).sum()),
            moved_more_than_25=("absolute_rank_change", lambda value: value.gt(25).sum()),
            moved_more_than_50=("absolute_rank_change", lambda value: value.gt(50).sum()),
        )
        .sort_values("baseline_rank_band", kind="stable")
    )
    by_family = (
        eligible.groupby("dominant_changed_family", as_index=False, dropna=False)
        .agg(
            rows=("ticker", "size"),
            median_absolute_rank_change=("absolute_rank_change", "median"),
            mean_absolute_rank_change=("absolute_rank_change", "mean"),
            median_composite_score_change=("composite_score_change", "median"),
        )
        .sort_values("rows", ascending=False, kind="stable")
    )
    return detail.sort_values(
        ["continuously_eligible", "absolute_rank_change", "ticker"],
        ascending=[False, False, True],
        kind="stable",
    ), by_band, by_family


def compare_variants(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Separate availability effects from score/rank normalization effects."""

    left = add_selection_rank(baseline)
    right = add_selection_rank(challenger)
    fields = [
        "decision_date", "ticker", "family_count", "family_cohort",
        "missing_families", "market_cap_band", "long_growth_v1_score",
        "selection_rank", "top10", "_top_eligible",
    ]
    detail = left[fields].merge(
        right[fields],
        on=["decision_date", "ticker"],
        how="outer",
        suffixes=("_baseline", "_challenger"),
        validate="one_to_one",
    )
    for suffix in ("baseline", "challenger"):
        detail[f"_top_eligible_{suffix}"] = _bool(
            detail[f"_top_eligible_{suffix}"]
        )
        detail[f"top10_{suffix}"] = _bool(detail[f"top10_{suffix}"])
    detail["score_change"] = (
        _numeric(detail, "long_growth_v1_score_challenger")
        - _numeric(detail, "long_growth_v1_score_baseline")
    )
    detail["rank_displacement"] = (
        _numeric(detail, "selection_rank_challenger")
        - _numeric(detail, "selection_rank_baseline")
    )
    detail["absolute_rank_displacement"] = detail["rank_displacement"].abs()
    gained = ~detail["_top_eligible_baseline"] & detail["_top_eligible_challenger"]
    lost = detail["_top_eligible_baseline"] & ~detail["_top_eligible_challenger"]
    family_changed = _numeric(detail, "family_count_baseline").ne(
        _numeric(detail, "family_count_challenger")
    )
    score_changed = detail["score_change"].abs().gt(1e-12)
    detail["effect_type"] = np.select(
        [gained, lost, family_changed, score_changed],
        [
            "availability_gain", "availability_loss",
            "family_availability_change", "score_only_change",
        ],
        default="unchanged",
    )

    top10_rows: list[dict[str, object]] = []
    for decision_date, group in detail.groupby("decision_date", sort=True):
        base = group.loc[group["top10_baseline"]].sort_values(
            "selection_rank_baseline"
        )["ticker"].tolist()
        chal = group.loc[group["top10_challenger"]].sort_values(
            "selection_rank_challenger"
        )["ticker"].tolist()
        overlap = len(set(base) & set(chal))
        top10_rows.append(
            {
                "decision_date": decision_date,
                "baseline_count": len(base),
                "challenger_count": len(chal),
                "overlap_count": overlap,
                "overlap_pct": overlap / 10,
                "identical_order": base == chal,
                "baseline_tickers": "|".join(base),
                "challenger_tickers": "|".join(chal),
            }
        )
    weekly_top10 = pd.DataFrame(top10_rows)

    turnover_rows: list[dict[str, object]] = []
    for variant in ("baseline", "challenger"):
        ranked = left if variant == "baseline" else right
        sets = {
            date: group.loc[group["top10"]].sort_values("selection_rank")["ticker"].tolist()
            for date, group in ranked.groupby("decision_date", sort=True)
        }
        dates = sorted(sets)
        for index, date in enumerate(dates):
            if index == 0:
                continue
            prior = sets[dates[index - 1]]
            current = sets[date]
            entrants = set(current) - set(prior)
            turnover_rows.append(
                {
                    "variant": variant,
                    "decision_date": date,
                    "prior_decision_date": dates[index - 1],
                    "prior_top10_count": len(prior),
                    "top10_count": len(current),
                    "overlap_count": len(set(current) & set(prior)),
                    "entrants_count": len(entrants),
                    "replacement_rate": len(entrants) / 10,
                    "comparison_valid": (
                        len(prior) == 10 and len(current) == 10
                    ),
                }
            )
    turnover = pd.DataFrame(turnover_rows)

    continuously_eligible = (
        detail["_top_eligible_baseline"]
        & detail["_top_eligible_challenger"]
        & _numeric(detail, "selection_rank_baseline").notna()
        & _numeric(detail, "selection_rank_challenger").notna()
    )
    rank_displacement = detail.loc[
        continuously_eligible,
        [
            "decision_date", "ticker", "selection_rank_baseline",
            "selection_rank_challenger", "rank_displacement",
            "absolute_rank_displacement", "score_change", "effect_type",
        ],
    ].copy()

    concentration_rows: list[dict[str, object]] = []
    for variant, ranked in (("baseline", left), ("challenger", right)):
        for decision_date, group in ranked.groupby("decision_date", sort=True):
            selected = group.loc[group["top10"]]
            counts = selected["market_cap_band"].value_counts(dropna=False)
            for band in [*MARKET_CAP_LABELS, "missing"]:
                count = int(counts.get(band, 0))
                concentration_rows.append(
                    {
                        "variant": variant,
                        "decision_date": decision_date,
                        "market_cap_band": band,
                        "top10_count": len(selected),
                        "band_count": count,
                        "band_share": count / len(selected) if len(selected) else np.nan,
                    }
                )
    concentration = pd.DataFrame(concentration_rows)
    return detail, weekly_top10, turnover, rank_displacement, concentration


def summarize_missingness_bias(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
    impact_detail: pd.DataFrame,
    weekly_top10: pd.DataFrame,
    turnover: pd.DataFrame,
    rank_displacement: pd.DataFrame,
    concentration: pd.DataFrame,
    forward_detail: pd.DataFrame,
    *,
    point_in_time_violations: int,
    classification_metadata_available: bool,
) -> MissingnessBiasSummary:
    panel = prepare_missingness_panel(challenger)
    baseline_panel = prepare_missingness_panel(baseline)
    current_date = panel["decision_date"].max()
    current = panel.loc[panel["decision_date"].eq(current_date)]
    baseline_current = baseline_panel.loc[
        baseline_panel["decision_date"].eq(current_date)
    ]
    row_count = max(len(current), 1)
    shares_gain = (
        _numeric(current, "shares_outstanding").gt(0).sum()
        - _numeric(baseline_current, "shares_outstanding").gt(0).sum()
    ) / row_count * 100
    valuation_gain = (
        _numeric(current, "valuation_score").notna().sum()
        - _numeric(baseline_current, "valuation_score").notna().sum()
    ) / row_count * 100
    current_top10 = weekly_top10.loc[
        weekly_top10["decision_date"].eq(current_date)
    ]
    current_rank = rank_displacement.loc[
        rank_displacement["decision_date"].eq(current_date),
        "absolute_rank_displacement",
    ]
    valid_turnover = turnover.loc[_bool(turnover["comparison_valid"])].copy()
    turnover_means = valid_turnover.groupby("variant")["replacement_rate"].mean()
    replacement_delta = (
        turnover_means.get("challenger", np.nan)
        - turnover_means.get("baseline", np.nan)
    ) * 100
    turnover_weekly = valid_turnover.pivot(
        index="decision_date", columns="variant", values="replacement_rate"
    )
    if {"baseline", "challenger"}.issubset(turnover_weekly.columns):
        max_replacement_delta = float(
            (
                turnover_weekly["challenger"]
                - turnover_weekly["baseline"]
            ).max()
            * 100
        )
    else:
        max_replacement_delta = np.nan
    populated_top10 = weekly_top10.loc[
        weekly_top10["baseline_count"].eq(10)
        & weekly_top10["challenger_count"].eq(10)
    ]
    comparable_dates = set(populated_top10["decision_date"])
    valid_concentration = concentration.loc[
        concentration["decision_date"].isin(comparable_dates)
    ]
    concentration_weekly = valid_concentration.pivot(
        index=["decision_date", "market_cap_band"],
        columns="variant",
        values="band_share",
    )
    if (
        len(populated_top10) >= 2
        and {"baseline", "challenger"}.issubset(concentration_weekly.columns)
    ):
        max_concentration = float(
            (
                concentration_weekly["challenger"]
                - concentration_weekly["baseline"]
            ).max()
            * 100
        )
    else:
        max_concentration = np.nan
    effects = impact_detail["effect_type"].value_counts()
    one_week = forward_detail.loc[forward_detail["horizon_weeks"].eq(1)]
    full_family_forward = forward_detail.loc[
        forward_detail["family_cohort"].eq("full_four_family")
    ]
    comparable_transitions = int(
        valid_turnover.groupby("decision_date")["variant"].nunique().eq(2).sum()
    )
    historical_top10_status = (
        "evaluated"
        if len(populated_top10) >= 2 and comparable_transitions >= 1
        else "not_evaluated_insufficient_populated_history"
    )
    forward_status = (
        "evaluated"
        if (
            one_week["decision_date"].nunique()
            >= PROMOTION_THRESHOLDS[
                "performance_min_nonoverlapping_weekly_observations"
            ]
            and not full_family_forward.empty
        )
        else "not_evaluated_insufficient_history_or_missing_full_family_cohort"
    )
    return MissingnessBiasSummary(
        universe_rows=len(panel),
        decision_dates=int(panel["decision_date"].nunique()),
        current_rows=len(current),
        current_full_four_family=int(current["family_count"].eq(4).sum()),
        current_three_family=int(current["family_count"].eq(3).sum()),
        current_fewer_than_three=int(current["family_count"].lt(3).sum()),
        current_return_price_present=int(
            _numeric(current, "return_price").gt(0).sum()
        ),
        current_shares_coverage_gain_percentage_points=float(shares_gain),
        current_valuation_coverage_gain_percentage_points=float(valuation_gain),
        current_top10_overlap=(
            int(current_top10.iloc[0]["overlap_count"])
            if not current_top10.empty else 0
        ),
        current_median_absolute_rank_displacement=(
            float(current_rank.median()) if not current_rank.empty else None
        ),
        mean_replacement_rate_delta_percentage_points=(
            float(replacement_delta) if pd.notna(replacement_delta) else None
        ),
        max_weekly_replacement_rate_delta_percentage_points=(
            max_replacement_delta
            if pd.notna(max_replacement_delta)
            else None
        ),
        max_market_cap_band_share_increase_percentage_points=(
            max_concentration if pd.notna(max_concentration) else None
        ),
        availability_gain_rows=int(effects.get("availability_gain", 0)),
        availability_loss_rows=int(effects.get("availability_loss", 0)),
        score_only_change_rows=int(effects.get("score_only_change", 0)),
        classification_metadata_available=classification_metadata_available,
        one_week_forward_decision_dates=int(one_week["decision_date"].nunique()),
        full_four_family_forward_observations=len(full_family_forward),
        populated_top10_comparison_dates=len(populated_top10),
        comparable_turnover_transitions=comparable_transitions,
        historical_top10_analysis_status=historical_top10_status,
        forward_return_analysis_status=forward_status,
        point_in_time_violations=point_in_time_violations,
    )


def summary_as_dict(summary: MissingnessBiasSummary) -> dict[str, object]:
    return asdict(summary)
