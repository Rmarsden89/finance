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
    configuration_version: int = 1
    mode: str = "research_only"
    broker_access_enabled: bool = False
    order_intents_enabled: bool = False
    order_review_enabled: bool = False
    order_placement_enabled: bool = False

    def validate(self) -> None:
        if self.model_id == self.source_champion:
            raise ValueError("V2 model_id must be distinct from the V1 champion")
        if self.mode != "research_only":
            raise ValueError("V2 scaffold only supports research_only mode")
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
        "allowed_stages": ["research_manifest"],
        "status": "RESEARCH_INITIALIZED",
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
