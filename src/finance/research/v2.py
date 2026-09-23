from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path


V2_ARTIFACT_ROOT = Path("reports") / "v2"


@dataclass(frozen=True)
class V2ResearchConfig:
    """Immutable safety contract for the first V2 research scaffold."""

    model_id: str = "long_growth_v2_research"
    source_champion: str = "long_growth_v1"
    configuration_version: int = 4
    mode: str = "research_only"
    dei_exact_share_fallback_enabled: bool = True
    dei_cover_date_fallback_enabled: bool = True
    nonpositive_liabilities_as_missing_enabled: bool = True
    broker_access_enabled: bool = False
    order_intents_enabled: bool = False
    order_review_enabled: bool = False
    order_placement_enabled: bool = False

    def validate(self) -> None:
        if self.model_id == self.source_champion:
            raise ValueError("V2 model_id must be distinct from the V1 champion")
        if self.mode != "research_only":
            raise ValueError("V2 scaffold only supports research_only mode")
        if (
            self.dei_cover_date_fallback_enabled
            and not self.dei_exact_share_fallback_enabled
        ):
            raise ValueError(
                "V2 DEI cover-date fallback requires exact DEI fallback"
            )
        enabled = {
            "broker_access_enabled": self.broker_access_enabled,
            "order_intents_enabled": self.order_intents_enabled,
            "order_review_enabled": self.order_review_enabled,
            "order_placement_enabled": self.order_placement_enabled,
        }
        unsafe = sorted(name for name, value in enabled.items() if value)
        if unsafe:
            raise ValueError(
                "V2 research configuration cannot enable execution capabilities: "
                + ", ".join(unsafe)
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)

    @property
    def configuration_hash(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


LONG_GROWTH_V2_RESEARCH = V2ResearchConfig()


def resolve_v2_run_dir(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> Path:
    """Return the isolated dated artifact directory for a V2 research run."""

    config.validate()
    return (
        repo_root.resolve()
        / V2_ARTIFACT_ROOT
        / config.model_id
        / as_of.isoformat()
    )


def build_v2_research_manifest(
    *,
    repo_root: Path,
    as_of: date,
    data_baseline_id: str,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
    created_at: datetime | None = None,
) -> dict[str, object]:
    """Build provenance for a research-only V2 run without touching a broker."""

    config.validate()
    baseline = data_baseline_id.strip()
    if not baseline:
        raise ValueError("data_baseline_id is required")

    created = created_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        raise ValueError("created_at must include a timezone")

    run_dir = resolve_v2_run_dir(repo_root, as_of, config=config)
    return {
        "schema_version": 1,
        "model_id": config.model_id,
        "source_champion": config.source_champion,
        "configuration_hash": config.configuration_hash,
        "data_baseline_id": baseline,
        "decision_date": as_of.isoformat(),
        "created_at": created.astimezone(timezone.utc).isoformat(),
        "mode": config.mode,
        "run_dir": str(run_dir),
        "execution_capabilities": {
            "broker_access": config.broker_access_enabled,
            "order_intents": config.order_intents_enabled,
            "order_review": config.order_review_enabled,
            "order_placement": config.order_placement_enabled,
        },
        "data_capabilities": {
            "dei_exact_share_fallback": (
                config.dei_exact_share_fallback_enabled
            ),
            "dei_cover_date_fallback": (
                config.dei_cover_date_fallback_enabled
            ),
            "nonpositive_liabilities_as_missing": (
                config.nonpositive_liabilities_as_missing_enabled
            ),
        },
        "allowed_stages": [
            "research_manifest",
            "sec_candidate_build",
            "sec_shadow_merge",
            "current_shadow_panel",
            "same_input_impact_comparison",
            "residual_shares_cleanup_audit",
            "targeted_sec_gap_refresh",
            "liabilities_gap_audit",
            "missingness_bias_audit",
            "ttm_reconstruction_diagnostic",
            "ttm_duration_cache_build",
            "ttm_numerator_validation",
            "ttm_outlier_audit",
            "ttm_q4_difference_diagnostic",
            "ttm_valuation_family_current",
        ],
        "status": "RESEARCH_INITIALIZED",
    }


def resolve_v2_sec_artifact_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return V2-only SEC and panel artifact paths for one decision date."""

    run_dir = resolve_v2_run_dir(repo_root, as_of, config=config)
    return {
        "run_dir": run_dir,
        "manifest": run_dir / "research_manifest.json",
        "input_fingerprints": run_dir / "input_fingerprints.json",
        "sec_candidates": run_dir / "sec_current_candidate_facts.csv",
        "sec_candidate_audit": run_dir / "sec_current_candidate_audit.csv",
        "sec_shadow": run_dir / "sec_winner_facts_shadow.csv",
        "sec_merge_audit": run_dir / "sec_shadow_merge_audit.csv",
        "sec_merge_summary": run_dir / "sec_shadow_merge_summary.csv",
        "share_quality_adjustments": run_dir / "sec_share_quality_adjustments.csv",
        "liabilities_quality_adjustments": (
            run_dir / "sec_liabilities_quality_adjustments.csv"
        ),
        "scoring_panel": run_dir / "current_shadow_scoring_panel.csv",
        "current_snapshot": run_dir / "current_shadow_snapshot.csv",
    }


def resolve_v2_impact_artifact_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for the same-input V1/V2 impact audit."""

    run_dir = resolve_v2_run_dir(repo_root, as_of, config=config)
    impact_dir = run_dir / "impact"
    baseline_dir = impact_dir / "v1_exact_only"
    challenger_dir = impact_dir / "v2_dei_cover_date"
    return {
        "run_dir": run_dir,
        "impact_dir": impact_dir,
        "baseline_dir": baseline_dir,
        "challenger_dir": challenger_dir,
        "baseline_candidates": baseline_dir / "sec_current_candidate_facts.csv",
        "baseline_candidate_audit": baseline_dir / "sec_current_candidate_audit.csv",
        "baseline_sec_shadow": baseline_dir / "sec_winner_facts_shadow.csv",
        "baseline_merge_audit": baseline_dir / "sec_shadow_merge_audit.csv",
        "baseline_merge_summary": baseline_dir / "sec_shadow_merge_summary.csv",
        "baseline_scoring_panel": baseline_dir / "current_shadow_scoring_panel.csv",
        "baseline_current_snapshot": baseline_dir / "current_shadow_snapshot.csv",
        "baseline_scored": baseline_dir / "long_growth_scored.csv",
        "challenger_scored": challenger_dir / "long_growth_scored.csv",
        "input_fingerprints": impact_dir / "input_fingerprints.json",
        "ticker_detail": impact_dir / "v1_v2_impact_by_ticker.csv",
        "top10_comparison": impact_dir / "v1_v2_top10_comparison.csv",
        "champion_regression": impact_dir / "v1_champion_regression.csv",
        "pit_audit": impact_dir / "v1_v2_pit_audit.csv",
        "summary_csv": impact_dir / "v1_v2_impact_summary.csv",
        "summary_json": impact_dir / "v1_v2_impact_summary.json",
    }


def resolve_v2_share_cleanup_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for residual shares cleanup evidence."""

    cleanup_dir = resolve_v2_run_dir(repo_root, as_of, config=config) / "cleanup"
    return {
        "cleanup_dir": cleanup_dir,
        "detail": cleanup_dir / "residual_shares_cleanup.csv",
        "summary": cleanup_dir / "residual_shares_cleanup_summary.json",
        "targeted_discovery": cleanup_dir / "targeted_discovery_retry.csv",
        "merged_discovery": cleanup_dir / "merged_discovery.csv",
    }


def resolve_v2_liabilities_audit_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for the V2 total-liabilities investigation."""

    audit_dir = resolve_v2_run_dir(repo_root, as_of, config=config) / "liabilities"
    return {
        "audit_dir": audit_dir,
        "detail": audit_dir / "liabilities_gap_audit.csv",
        "summary": audit_dir / "liabilities_gap_summary.json",
        "classification_summary": audit_dir / "liabilities_classification_summary.csv",
        "coverage_by_year": audit_dir / "liabilities_coverage_by_year.csv",
        "coverage_by_sector": audit_dir / "liabilities_coverage_by_sector.csv",
        "identity_gap_candidates": audit_dir / "identity_gap_candidates.csv",
        "identity_validation": audit_dir / "identity_validation.csv",
        "identity_validation_summary": audit_dir / "identity_validation_summary.csv",
        "alternate_tag_summary": audit_dir / "alternate_liability_tag_summary.csv",
        "input_fingerprints": audit_dir / "input_fingerprints.json",
    }


def resolve_v2_missingness_audit_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for the V2 missing-data selection audit."""

    audit_dir = resolve_v2_run_dir(repo_root, as_of, config=config) / "missingness"
    return {
        "audit_dir": audit_dir,
        "coverage_by_year": audit_dir / "family_input_coverage_by_year.csv",
        "current_coverage": audit_dir / "current_family_input_coverage.csv",
        "cohorts": audit_dir / "missingness_cohorts.csv",
        "cohorts_by_market_cap": audit_dir / "current_cohorts_by_market_cap_band.csv",
        "cohorts_by_classification": audit_dir / "current_cohorts_by_classification.csv",
        "forward_detail": audit_dir / "cohort_forward_returns.csv",
        "forward_summary": audit_dir / "cohort_forward_return_summary.csv",
        "impact_detail": audit_dir / "availability_vs_score_effects.csv",
        "weekly_top10": audit_dir / "weekly_top10_comparison.csv",
        "turnover": audit_dir / "weekly_top10_turnover.csv",
        "rank_displacement": audit_dir / "rank_displacement.csv",
        "concentration": audit_dir / "top10_market_cap_concentration.csv",
        "thresholds": audit_dir / "promotion_thresholds.json",
        "summary": audit_dir / "missingness_bias_summary.json",
        "conclusion": audit_dir / "conclusion.md",
        "input_fingerprints": audit_dir / "input_fingerprints.json",
    }


def resolve_v2_missingness_history_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for history-qualified Issue #5 evidence."""

    history_dir = (
        resolve_v2_run_dir(repo_root, as_of, config=config)
        / "missingness"
        / "history"
    )
    return {
        "history_dir": history_dir,
        "coverage_by_year": history_dir / "historical_family_input_coverage_by_year.csv",
        "cohorts_by_year": history_dir / "historical_missingness_cohorts_by_year.csv",
        "cohorts_by_market_cap": history_dir / "historical_cohorts_by_market_cap_band.csv",
        "cohorts_by_classification": history_dir / "historical_cohorts_by_classification.csv",
        "forward_summary": history_dir / "historical_cohort_forward_return_summary.csv",
        "weekly_top10": history_dir / "historical_weekly_top10.csv",
        "turnover": history_dir / "historical_weekly_top10_turnover.csv",
        "concentration": history_dir / "historical_top10_market_cap_concentration.csv",
        "rank_detail": history_dir / "current_rank_shift_detail.csv",
        "rank_by_band": history_dir / "current_rank_shift_by_band.csv",
        "rank_by_family": history_dir / "current_rank_shift_by_family.csv",
        "pit_audit": history_dir / "historical_pit_audit.csv",
        "summary": history_dir / "history_qualified_summary.json",
        "conclusion": history_dir / "conclusion.md",
        "input_fingerprints": history_dir / "input_fingerprints.json",
    }



def resolve_v2_ttm_diagnostic_paths(
    repo_root: Path,
    as_of: date,
    *,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> dict[str, Path]:
    """Return isolated paths for Issue #6 TTM reconstruction diagnostics.\n\n    The enriched reconstruction namespace is policy-specific so direct- and\n    YTD-preferred evidence can coexist without overwriting prior artifacts.\n    """

    diagnostic_dir = resolve_v2_run_dir(
        repo_root, as_of, config=config
    ) / "ttm"
    return {
        "diagnostic_dir": diagnostic_dir,
        "quarters": diagnostic_dir / "discrete_quarters.csv",
        "reconstruction_audit": diagnostic_dir / "reconstruction_audit.csv",
        "group_coverage": diagnostic_dir / "quarter_group_coverage.csv",
        "coverage_by_fiscal_year": (
            diagnostic_dir / "quarter_coverage_by_fiscal_year.csv"
        ),
        "derivation_summary": diagnostic_dir / "derivation_summary.csv",
        "rejection_summary": diagnostic_dir / "rejection_reason_summary.csv",
        "amendment_groups": diagnostic_dir / "amendment_groups.csv",
        "fiscal_calendar_summary": (
            diagnostic_dir / "fiscal_calendar_summary.csv"
        ),
        "pit_audit": diagnostic_dir / "pit_audit.csv",
        "summary": diagnostic_dir / "reconstruction_summary.json",
        "input_fingerprints": diagnostic_dir / "input_fingerprints.json",
        "duration_cache_dir": diagnostic_dir / "duration_cache",
        "duration_quarter_cache_dir": (
            diagnostic_dir / "duration_cache" / "quarters"
        ),
        "duration_winners": (
            diagnostic_dir / "duration_cache" / "ttm_duration_winners.csv"
        ),
        "duration_winner_audit": (
            diagnostic_dir / "duration_cache" / "ttm_duration_winner_audit.csv"
        ),
        "duration_summary": (
            diagnostic_dir / "duration_cache" / "summary.json"
        ),
        "duration_input_fingerprints": (
            diagnostic_dir / "duration_cache" / "input_fingerprints.json"
        ),
        "enriched_reconstruction_dir": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
        ),
        "enriched_quarters": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "discrete_quarters.csv"
        ),
        "enriched_reconstruction_audit": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "reconstruction_audit.csv"
        ),
        "enriched_group_coverage": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "quarter_group_coverage.csv"
        ),
        "enriched_coverage_by_fiscal_year": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "quarter_coverage_by_fiscal_year.csv"
        ),
        "enriched_derivation_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "derivation_summary.csv"
        ),
        "enriched_rejection_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "rejection_reason_summary.csv"
        ),
        "enriched_amendment_groups": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "amendment_groups.csv"
        ),
        "enriched_fiscal_calendar_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "fiscal_calendar_summary.csv"
        ),
        "enriched_pit_audit": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "pit_audit.csv"
        ),
        "enriched_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "reconstruction_summary.json"
        ),
        "enriched_input_fingerprints": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "input_fingerprints.json"
        ),
        "ttm_validation_dir": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
        ),
        "ttm_values": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "ttm_values.csv"
        ),
        "ttm_latest_by_concept": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "latest_ttm_by_concept.csv"
        ),
        "ttm_current_numerators": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "current_ttm_numerators.csv"
        ),
        "ttm_annual_comparison": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "annual_vs_ttm_numerators.csv"
        ),
        "ttm_construction_audit": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "ttm_construction_audit.csv"
        ),
        "ttm_rejection_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "ttm_rejection_summary.csv"
        ),
        "ttm_validation_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "summary.json"
        ),
        "ttm_validation_fingerprints": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "input_fingerprints.json"
        ),
        "ttm_outlier_dir": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit"
        ),
        "ttm_outliers": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "annual_vs_ttm_outliers.csv"
        ),
        "ttm_outlier_lineage": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "outlier_ttm_lineage.csv"
        ),
        "ttm_q4_reconciliation": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_ttm_vs_reported_annual.csv"
        ),
        "ttm_q4_reconciliation_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_reconciliation_summary.csv"
        ),
        "ttm_missing_coverage": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "current_missing_ttm_coverage.csv"
        ),
        "ttm_missing_coverage_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "current_missing_ttm_summary.csv"
        ),
        "ttm_outlier_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "summary.json"
        ),
        "ttm_outlier_fingerprints": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "input_fingerprints.json"
        ),
        "ttm_q4_difference_dir": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
        ),
        "ttm_q4_difference_detail": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "q4_material_difference_detail.csv"
        ),
        "ttm_q4_difference_by_magnitude": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "difference_by_magnitude.csv"
        ),
        "ttm_q4_difference_by_derivation": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "difference_by_derivation.csv"
        ),
        "ttm_q4_difference_by_tag_continuity": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "difference_by_tag_continuity.csv"
        ),
        "ttm_q4_difference_by_amendment": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "difference_by_amendment.csv"
        ),
        "ttm_q4_difference_repeat_ciks": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "repeat_ciks.csv"
        ),
        "ttm_q4_difference_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "summary.json"
        ),
        "ttm_q4_difference_fingerprints": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred" / "ttm_validation"
            / "outlier_audit" / "q4_difference_diagnostic"
            / "input_fingerprints.json"
        ),
        "ttm_valuation_family_dir": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
        ),
        "ttm_valuation_factor_detail": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "factor_detail.csv"
        ),
        "ttm_valuation_distribution_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "factor_distribution_summary.csv"
        ),
        "ttm_valuation_family_comparison": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "family_comparison.csv"
        ),
        "ttm_valuation_rank_comparison": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "rank_comparison.csv"
        ),
        "ttm_valuation_correlations": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "factor_correlations.csv"
        ),
        "ttm_valuation_top10": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "valuation_family_top10_comparison.csv"
        ),
        "ttm_valuation_summary": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "summary.json"
        ),
        "ttm_valuation_fingerprints": (
            diagnostic_dir / "enriched_reconstruction_ytd_preferred"
            / "ttm_validation" / "valuation_family_current"
            / "input_fingerprints.json"
        ),
    }

def write_v2_research_manifest(
    *,
    repo_root: Path,
    as_of: date,
    data_baseline_id: str,
    config: V2ResearchConfig = LONG_GROWTH_V2_RESEARCH,
) -> Path:
    """Write the V2 run manifest under reports/v2 and return its path."""

    manifest = build_v2_research_manifest(
        repo_root=repo_root,
        as_of=as_of,
        data_baseline_id=data_baseline_id,
        config=config,
    )
    run_dir = resolve_v2_run_dir(repo_root, as_of, config=config)
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "research_manifest.json"
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
