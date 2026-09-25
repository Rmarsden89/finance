from finance.research.v3 import (
    V3_LIABILITIES_APPROVED_ISSUERS,
    V3_LIABILITIES_RULE,
    validate_v3_liabilities_freeze,
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
