from finance.research.v4 import (
    V4_DECK_LIABILITIES_RULE,
    V4_LIABILITIES_APPROVED_ISSUERS,
    validate_v4_deck_liabilities_freeze,
    v4_liabilities_approved_ciks,
    v4_liabilities_approved_tickers,
)


def test_v4_deck_liabilities_rule_is_exact_and_research_only() -> None:
    validate_v4_deck_liabilities_freeze()

    rule = V4_DECK_LIABILITIES_RULE
    assert rule.rule_id == "v4_deck_current_plus_noncurrent_post_2015q1_v1"
    assert rule.mode == "research_only"
    assert rule.source == "SEC"
    assert rule.ticker == "DECK"
    assert rule.cik == 910521
    assert rule.first_supported_period == "2015-03-31"
    assert rule.last_material_validation_period == "2014-12-31"
    assert rule.current_tag == "LiabilitiesCurrent"
    assert rule.noncurrent_tag == "LiabilitiesNoncurrent"
    assert rule.unit == "USD"
    assert rule.require_same_accession is True
    assert rule.require_same_period is True
    assert rule.require_same_instant is True
    assert rule.require_undimensioned is True
    assert rule.require_unique_positive_values is True
    assert rule.require_pit_eligibility is True
    assert rule.require_period_on_or_after_first_supported_period is True
    assert rule.overwrite_positive_canonical_liabilities is False
    assert rule.assets_minus_equity_recovery_allowed is False
    assert rule.total_like_minus_equity_recovery_allowed is False
    assert rule.paid_vendor_allowed is False
    assert rule.broker_access_enabled is False
    assert rule.order_intents_enabled is False
    assert rule.order_review_enabled is False
    assert rule.order_placement_enabled is False
    assert len(rule.configuration_hash) == 64


def test_v4_liabilities_issuer_freeze_is_deck_only() -> None:
    validate_v4_deck_liabilities_freeze()

    assert len(V4_LIABILITIES_APPROVED_ISSUERS) == 1
    assert v4_liabilities_approved_tickers() == frozenset({"DECK"})
    assert v4_liabilities_approved_ciks() == frozenset({910521})
