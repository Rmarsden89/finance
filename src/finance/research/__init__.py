"""Research-only infrastructure for challenger model versions."""

from .v2 import (
    LONG_GROWTH_V2_RESEARCH,
    V2ResearchConfig,
    build_v2_research_manifest,
    resolve_v2_run_dir,
    write_v2_research_manifest,
)

__all__ = [
    "LONG_GROWTH_V2_RESEARCH",
    "V2ResearchConfig",
    "build_v2_research_manifest",
    "resolve_v2_run_dir",
    "write_v2_research_manifest",
]
