from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchConfig:
    """Top-level constraints shared by backtests and later live research."""

    weekly_budget: float = 10.0
    benchmark_symbol: str = "VOO"
    decision_frequency: str = "weekly"
    allow_leverage: bool = False
    allow_margin: bool = False
    allow_options: bool = False


DEFAULT_CONFIG = ResearchConfig()
