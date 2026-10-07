from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class V4LiabilitiesIssuer:
    ticker: str
    cik: int


@dataclass(frozen=True)
class V4DeckLiabilitiesRuleConfig:
    """Frozen research-only V4 DECK liabilities recovery contract."""

    rule_id: str = "v4_deck_current_plus_noncurrent_post_2015q1_v1"
    evidence_as_of: str = "2026-09-15"
    mode: str = "research_only"
    source: str = "SEC"
    ticker: str = "DECK"
    cik: int = 910521
    first_supported_period: str = "2015-03-31"
    last_material_validation_period: str = "2014-12-31"
    current_tag: str = "LiabilitiesCurrent"
    noncurrent_tag: str = "LiabilitiesNoncurrent"
    unit: str = "USD"
    require_same_accession: bool = True
    require_same_period: bool = True
    require_same_instant: bool = True
    require_undimensioned: bool = True
    require_unique_positive_values: bool = True
    require_pit_eligibility: bool = True
    require_period_on_or_after_first_supported_period: bool = True
    overwrite_positive_canonical_liabilities: bool = False
    assets_minus_equity_recovery_allowed: bool = False
    total_like_minus_equity_recovery_allowed: bool = False
    paid_vendor_allowed: bool = False
    broker_access_enabled: bool = False
    order_intents_enabled: bool = False
    order_review_enabled: bool = False
    order_placement_enabled: bool = False

    def validate(self) -> None:
        if self.mode != "research_only":
            raise ValueError("Frozen V4 liabilities rule only supports research_only")
        if self.source != "SEC":
            raise ValueError("Frozen V4 liabilities rule is SEC-only")
        if self.ticker != "DECK" or self.cik != 910521:
            raise ValueError("Frozen V4 liabilities rule is DECK/CIK 910521 only")
        if self.first_supported_period != "2015-03-31":
            raise ValueError("Frozen V4 DECK regime must begin 2015-03-31")
        if self.last_material_validation_period != "2014-12-31":
            raise ValueError("Frozen V4 DECK last material period must be 2014-12-31")
        if not self.require_period_on_or_after_first_supported_period:
            raise ValueError("Frozen V4 DECK rule must enforce the regime start")
        if self.assets_minus_equity_recovery_allowed:
            raise ValueError("Assets - Equity is validation-only, never recovery")
        if self.total_like_minus_equity_recovery_allowed:
            raise ValueError(
                "LiabilitiesAndStockholdersEquity - Equity is validation-only"
            )
        if self.overwrite_positive_canonical_liabilities:
            raise ValueError("Frozen V4 rule may not overwrite canonical liabilities")
        if self.paid_vendor_allowed:
            raise ValueError("Paid vendors are not allowed by this frozen V4 rule")
        execution = {
            "broker_access_enabled": self.broker_access_enabled,
            "order_intents_enabled": self.order_intents_enabled,
            "order_review_enabled": self.order_review_enabled,
            "order_placement_enabled": self.order_placement_enabled,
        }
        unsafe = sorted(name for name, value in execution.items() if value)
        if unsafe:
            raise ValueError(
                "Frozen V4 research rule cannot enable execution capabilities: "
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


V4_DECK_LIABILITIES_RULE = V4DeckLiabilitiesRuleConfig()
V4_LIABILITIES_APPROVED_ISSUERS: tuple[V4LiabilitiesIssuer, ...] = (
    V4LiabilitiesIssuer("DECK", 910521),
)


def validate_v4_deck_liabilities_freeze() -> None:
    """Validate immutable V4 DECK liabilities rule and issuer boundary."""

    V4_DECK_LIABILITIES_RULE.validate()
    if len(V4_LIABILITIES_APPROVED_ISSUERS) != 1:
        raise ValueError("Frozen V4 liabilities issuer set must contain one issuer")
    issuer = V4_LIABILITIES_APPROVED_ISSUERS[0]
    if issuer.ticker != V4_DECK_LIABILITIES_RULE.ticker:
        raise ValueError("V4 liabilities ticker drift detected")
    if issuer.cik != V4_DECK_LIABILITIES_RULE.cik:
        raise ValueError("V4 liabilities CIK drift detected")


def v4_liabilities_approved_ciks() -> frozenset[int]:
    validate_v4_deck_liabilities_freeze()
    return frozenset(issuer.cik for issuer in V4_LIABILITIES_APPROVED_ISSUERS)


def v4_liabilities_approved_tickers() -> frozenset[str]:
    validate_v4_deck_liabilities_freeze()
    return frozenset(issuer.ticker for issuer in V4_LIABILITIES_APPROVED_ISSUERS)
