from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class ApprovedLiabilitiesIssuer:
    ticker: str
    cik: int


@dataclass(frozen=True)
class V3LiabilitiesRuleConfig:
    """Frozen research-only contract for the first V3 liabilities candidate."""

    rule_id: str = "v3_liabilities_current_plus_noncurrent_v1"
    evidence_as_of: str = "2026-09-15"
    mode: str = "research_only"
    source: str = "SEC"
    direct_tag: str = "Liabilities"
    current_tag: str = "LiabilitiesCurrent"
    noncurrent_tag: str = "LiabilitiesNoncurrent"
    unit: str = "USD"
    require_same_accession: bool = True
    require_same_period: bool = True
    require_same_instant: bool = True
    require_undimensioned: bool = True
    require_unique_positive_values: bool = True
    require_pit_eligibility: bool = True
    overwrite_positive_canonical_liabilities: bool = False
    assets_minus_equity_recovery_allowed: bool = False
    paid_vendor_allowed: bool = False
    broker_access_enabled: bool = False
    order_intents_enabled: bool = False
    order_review_enabled: bool = False
    order_placement_enabled: bool = False

    def validate(self) -> None:
        if self.mode != "research_only":
            raise ValueError("Frozen V3 liabilities rule only supports research_only")
        if self.source != "SEC":
            raise ValueError("Frozen V3 liabilities rule is SEC-only")
        if self.assets_minus_equity_recovery_allowed:
            raise ValueError("Assets - Equity is validation-only, never recovery")
        if self.paid_vendor_allowed:
            raise ValueError("Paid vendors are not allowed by this frozen rule")
        if self.overwrite_positive_canonical_liabilities:
            raise ValueError("Frozen V3 rule may not overwrite canonical liabilities")
        execution = {
            "broker_access_enabled": self.broker_access_enabled,
            "order_intents_enabled": self.order_intents_enabled,
            "order_review_enabled": self.order_review_enabled,
            "order_placement_enabled": self.order_placement_enabled,
        }
        unsafe = sorted(name for name, value in execution.items() if value)
        if unsafe:
            raise ValueError(
                "Frozen V3 research rule cannot enable execution capabilities: "
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



@dataclass(frozen=True)
class V3SharesRuleConfig:
    """Frozen research-only contract for the first V3 raw-SEC shares candidate."""

    rule_id: str = "v3_raw_sec_entity_common_shares_v1"
    evidence_as_of: str = "2026-09-15"
    mode: str = "research_only"
    source: str = "SEC"
    source_tag: str = "EntityCommonStockSharesOutstanding"
    unit_contains: str = "share"
    supported_forms: tuple[str, ...] = (
        "10-K",
        "10-K/A",
        "10-Q",
        "10-Q/A",
        "20-F",
        "20-F/A",
        "40-F",
        "40-F/A",
    )
    qtrs_required: int = 0
    require_positive_value: bool = True
    require_nonfuture_context_instant: bool = True
    prefer_undimensioned: bool = True
    allow_recognized_share_class_sum: bool = True
    allowed_member_terms: tuple[str, ...] = (
        "commonstockmember",
        "commonstockclass",
        "commonclass",
        "nonvotingcommonstockmember",
        "preferredstockmember",
    )
    reject_coreg: bool = True
    require_unique_undimensioned_value: bool = True
    require_unique_value_per_share_class: bool = True
    require_pit_eligibility: bool = True
    overwrite_positive_canonical_shares: bool = False
    paid_vendor_allowed: bool = False
    broker_access_enabled: bool = False
    order_intents_enabled: bool = False
    order_review_enabled: bool = False
    order_placement_enabled: bool = False

    def validate(self) -> None:
        if self.mode != "research_only":
            raise ValueError("Frozen V3 shares rule only supports research_only")
        if self.source != "SEC":
            raise ValueError("Frozen V3 shares rule is SEC-only")
        if self.source_tag != "EntityCommonStockSharesOutstanding":
            raise ValueError("Frozen V3 shares rule uses the DEI entity shares tag")
        if self.qtrs_required != 0:
            raise ValueError("Frozen V3 shares rule requires instant facts")
        if not self.prefer_undimensioned:
            raise ValueError("Frozen V3 shares rule must prefer undimensioned facts")
        if not self.allow_recognized_share_class_sum:
            raise ValueError("Frozen V3 shares rule requires validated class summation")
        if not self.reject_coreg:
            raise ValueError("Frozen V3 shares rule must reject coreg facts")
        if self.overwrite_positive_canonical_shares:
            raise ValueError("Frozen V3 shares rule may not overwrite canonical shares")
        if self.paid_vendor_allowed:
            raise ValueError("Paid vendors are not allowed by this frozen rule")
        execution = {
            "broker_access_enabled": self.broker_access_enabled,
            "order_intents_enabled": self.order_intents_enabled,
            "order_review_enabled": self.order_review_enabled,
            "order_placement_enabled": self.order_placement_enabled,
        }
        unsafe = sorted(name for name, value in execution.items() if value)
        if unsafe:
            raise ValueError(
                "Frozen V3 research rule cannot enable execution capabilities: "
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


V3_SHARES_RULE = V3SharesRuleConfig()


def validate_v3_shares_freeze() -> None:
    """Validate immutable V3 shares rule and safety invariants."""

    V3_SHARES_RULE.validate()
    if not V3_SHARES_RULE.allowed_member_terms:
        raise ValueError("Frozen V3 shares member allowlist may not be empty")
    if len(set(V3_SHARES_RULE.allowed_member_terms)) != len(
        V3_SHARES_RULE.allowed_member_terms
    ):
        raise ValueError("Frozen V3 shares member terms must be unique")
    if not V3_SHARES_RULE.supported_forms:
        raise ValueError("Frozen V3 shares supported forms may not be empty")


V3_LIABILITIES_APPROVED_ISSUERS: tuple[ApprovedLiabilitiesIssuer, ...] = (
    ApprovedLiabilitiesIssuer("ADI", 6281),
    ApprovedLiabilitiesIssuer("CDNS", 813672),
    ApprovedLiabilitiesIssuer("CDW", 1402057),
    ApprovedLiabilitiesIssuer("CMS", 811156),
    ApprovedLiabilitiesIssuer("CTAS", 723254),
    ApprovedLiabilitiesIssuer("CTVA", 1755672),
    ApprovedLiabilitiesIssuer("DAL", 27904),
    ApprovedLiabilitiesIssuer("ETN", 1551182),
    ApprovedLiabilitiesIssuer("ETR", 65984),
    ApprovedLiabilitiesIssuer("EVRG", 1711269),
    ApprovedLiabilitiesIssuer("FFIV", 1048695),
    ApprovedLiabilitiesIssuer("GD", 40533),
    ApprovedLiabilitiesIssuer("ITW", 49826),
    ApprovedLiabilitiesIssuer("LLY", 59478),
    ApprovedLiabilitiesIssuer("ORCL", 1341439),
    ApprovedLiabilitiesIssuer("PKG", 75677),
    ApprovedLiabilitiesIssuer("SYY", 96021),
    ApprovedLiabilitiesIssuer("TGT", 27419),
    ApprovedLiabilitiesIssuer("TMUS", 1283699),
    ApprovedLiabilitiesIssuer("VZ", 732712),
    ApprovedLiabilitiesIssuer("WEC", 783325),
)

V3_LIABILITIES_RULE = V3LiabilitiesRuleConfig()


def validate_v3_liabilities_freeze() -> None:
    """Validate immutable issuer identity and safety invariants."""

    V3_LIABILITIES_RULE.validate()
    if len(V3_LIABILITIES_APPROVED_ISSUERS) != 21:
        raise ValueError("Frozen V3 liabilities issuer set must contain 21 issuers")

    tickers = [issuer.ticker for issuer in V3_LIABILITIES_APPROVED_ISSUERS]
    ciks = [issuer.cik for issuer in V3_LIABILITIES_APPROVED_ISSUERS]
    if len(set(tickers)) != len(tickers):
        raise ValueError("Frozen V3 liabilities issuer tickers must be unique")
    if len(set(ciks)) != len(ciks):
        raise ValueError("Frozen V3 liabilities issuer CIKs must be unique")
    if any(not ticker or ticker != ticker.upper() for ticker in tickers):
        raise ValueError("Frozen V3 liabilities tickers must be uppercase")
    if any(cik <= 0 for cik in ciks):
        raise ValueError("Frozen V3 liabilities CIKs must be positive")


def v3_liabilities_approved_ciks() -> frozenset[int]:
    validate_v3_liabilities_freeze()
    return frozenset(
        issuer.cik for issuer in V3_LIABILITIES_APPROVED_ISSUERS
    )


def v3_liabilities_approved_tickers() -> frozenset[str]:
    validate_v3_liabilities_freeze()
    return frozenset(
        issuer.ticker for issuer in V3_LIABILITIES_APPROVED_ISSUERS
    )
