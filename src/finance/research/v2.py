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
    configuration_version: int = 3
    mode: str = "research_only"
    dei_exact_share_fallback_enabled: bool = True
    dei_cover_date_fallback_enabled: bool = True
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
        },
        "allowed_stages": [
            "research_manifest",
            "sec_candidate_build",
            "sec_shadow_merge",
            "current_shadow_panel",
            "same_input_impact_comparison",
            "residual_shares_cleanup_audit",
            "targeted_sec_gap_refresh",
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
