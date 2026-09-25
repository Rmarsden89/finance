from pathlib import Path

from finance.research.v3 import (
    V3_COMBINED_CHALLENGER,
    V3_LIABILITIES_APPROVED_ISSUERS,
    V3_LIABILITIES_RULE,
    V3_SHARES_RULE,
    validate_v3_combined_challenger_freeze,
    validate_v3_liabilities_freeze,
    validate_v3_shares_freeze,
    v3_liabilities_approved_ciks,
    v3_liabilities_approved_tickers,
)


def test_v3_liabilities_rule_is_research_only_and_fail_closed() -> None:
    validate_v3_liabilities_freeze()

    assert V3_LIABILITIES_RULE.mode == "research_only"
    assert V3_LIABILITIES_RULE.source == "SEC"
    assert V3_LIABILITIES_RULE.require_same_accession is True
    assert V3_LIABILITIES_RULE.require_same_period is True
    assert V3_LIABILITIES_RULE.require_same_instant is True
    assert V3_LIABILITIES_RULE.require_undimensioned is True
    assert V3_LIABILITIES_RULE.require_unique_positive_values is True
    assert V3_LIABILITIES_RULE.require_pit_eligibility is True
    assert V3_LIABILITIES_RULE.overwrite_positive_canonical_liabilities is False
    assert V3_LIABILITIES_RULE.assets_minus_equity_recovery_allowed is False
    assert V3_LIABILITIES_RULE.paid_vendor_allowed is False
    assert V3_LIABILITIES_RULE.broker_access_enabled is False
    assert V3_LIABILITIES_RULE.order_placement_enabled is False


def test_v3_liabilities_issuer_freeze_is_exact() -> None:
    expected = {
        ("ADI", 6281),
        ("CDNS", 813672),
        ("CDW", 1402057),
        ("CMS", 811156),
        ("CTAS", 723254),
        ("CTVA", 1755672),
        ("DAL", 27904),
        ("ETN", 1551182),
        ("ETR", 65984),
        ("EVRG", 1711269),
        ("FFIV", 1048695),
        ("GD", 40533),
        ("ITW", 49826),
        ("LLY", 59478),
        ("ORCL", 1341439),
        ("PKG", 75677),
        ("SYY", 96021),
        ("TGT", 27419),
        ("TMUS", 1283699),
        ("VZ", 732712),
        ("WEC", 783325),
    }

    actual = {
        (issuer.ticker, issuer.cik)
        for issuer in V3_LIABILITIES_APPROVED_ISSUERS
    }
    assert actual == expected
    assert len(v3_liabilities_approved_ciks()) == 21
    assert len(v3_liabilities_approved_tickers()) == 21



def test_v3_shares_rule_is_research_only_and_fail_closed() -> None:
    validate_v3_shares_freeze()

    assert V3_SHARES_RULE.rule_id == "v3_raw_sec_entity_common_shares_v1"
    assert V3_SHARES_RULE.mode == "research_only"
    assert V3_SHARES_RULE.source == "SEC"
    assert V3_SHARES_RULE.source_tag == "EntityCommonStockSharesOutstanding"
    assert V3_SHARES_RULE.qtrs_required == 0
    assert V3_SHARES_RULE.require_positive_value is True
    assert V3_SHARES_RULE.require_nonfuture_context_instant is True
    assert V3_SHARES_RULE.prefer_undimensioned is True
    assert V3_SHARES_RULE.allow_recognized_share_class_sum is True
    assert V3_SHARES_RULE.reject_coreg is True
    assert V3_SHARES_RULE.require_unique_undimensioned_value is True
    assert V3_SHARES_RULE.require_unique_value_per_share_class is True
    assert V3_SHARES_RULE.require_pit_eligibility is True
    assert V3_SHARES_RULE.overwrite_positive_canonical_shares is False
    assert V3_SHARES_RULE.paid_vendor_allowed is False
    assert V3_SHARES_RULE.broker_access_enabled is False
    assert V3_SHARES_RULE.order_intents_enabled is False
    assert V3_SHARES_RULE.order_review_enabled is False
    assert V3_SHARES_RULE.order_placement_enabled is False


def test_v3_shares_rule_has_frozen_supported_forms_and_member_terms() -> None:
    validate_v3_shares_freeze()

    assert set(V3_SHARES_RULE.supported_forms) == {
        "10-K",
        "10-K/A",
        "10-Q",
        "10-Q/A",
        "20-F",
        "20-F/A",
        "40-F",
        "40-F/A",
    }
    assert set(V3_SHARES_RULE.allowed_member_terms) == {
        "commonstockmember",
        "commonstockclass",
        "commonclass",
        "nonvotingcommonstockmember",
        "preferredstockmember",
    }
    assert len(V3_SHARES_RULE.configuration_hash) == 64



def test_v3_combined_challenger_contract_is_exact_and_shadow_only() -> None:
    validate_v3_combined_challenger_freeze()

    assert V3_COMBINED_CHALLENGER.model_id == "long_growth_v3_data_coverage_v1"
    assert V3_COMBINED_CHALLENGER.mode == "shadow_only"
    assert (
        V3_COMBINED_CHALLENGER.foundation_model_id
        == "long_growth_v2_ttm_valuation_v1"
    )
    assert (
        V3_COMBINED_CHALLENGER.liabilities_rule_id
        == V3_LIABILITIES_RULE.rule_id
    )
    assert (
        V3_COMBINED_CHALLENGER.liabilities_rule_hash
        == V3_LIABILITIES_RULE.configuration_hash
    )
    assert V3_COMBINED_CHALLENGER.shares_rule_id == V3_SHARES_RULE.rule_id
    assert (
        V3_COMBINED_CHALLENGER.shares_rule_hash
        == V3_SHARES_RULE.configuration_hash
    )
    assert V3_COMBINED_CHALLENGER.factor_definitions_changed is False
    assert V3_COMBINED_CHALLENGER.family_weights_changed is False
    assert V3_COMBINED_CHALLENGER.family_minimums_changed is False
    assert V3_COMBINED_CHALLENGER.eligibility_rules_changed is False
    assert V3_COMBINED_CHALLENGER.portfolio_construction_changed is False
    assert V3_COMBINED_CHALLENGER.broker_access_enabled is False
    assert V3_COMBINED_CHALLENGER.order_intents_enabled is False
    assert V3_COMBINED_CHALLENGER.order_review_enabled is False
    assert V3_COMBINED_CHALLENGER.order_placement_enabled is False


def test_v3_combined_challenger_hash_is_stable_shape() -> None:
    validate_v3_combined_challenger_freeze()

    assert len(V3_COMBINED_CHALLENGER.configuration_hash) == 64
    assert V3_COMBINED_CHALLENGER.configuration_hash.isalnum()



def test_v3_current_comparison_and_verifier_have_no_execution_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = [
        root / "scripts" / "compare_v2_v3_current.py",
        root / "scripts" / "verify_v3_current_determinism.py",
    ]
    prohibited = (
        "robinhood",
        "place_equity_order",
        "review_equity_order",
        "order_intent",
        "run_v1_submit",
        "run_v1_presubmit",
    )

    for path in scripts:
        text = path.read_text(encoding="utf-8").lower()
        assert all(token not in text for token in prohibited)
