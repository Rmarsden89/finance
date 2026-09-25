import pandas as pd

from finance.research.v3_data_sources import (
    build_overlap_validation_sample,
    build_residual_gap_inventory,
    source_candidate_template,
    summarize_gap_inventory,
)


def test_build_residual_gap_inventory_tracks_overlap():
    shares = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "cik": 1,
                "company_name": "AAA Co",
                "shares_outstanding": None,
                "classification": "no_supported_current_share_fact",
                "recommended_action": "documented_no_supported_fact",
            },
            {
                "ticker": "BBB",
                "cik": 2,
                "company_name": "BBB Co",
                "shares_outstanding": None,
                "classification": "no_supported_current_share_fact",
                "recommended_action": "documented_no_supported_fact",
            },
        ]
    )
    liabilities = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "cik": 1,
                "company_name": "AAA Co",
                "total_liabilities": None,
                "classification": "unsupported_alternate_liability_tag_present",
                "recommended_action": "research_alternate_tag",
            }
        ]
    )

    inventory, summary = build_residual_gap_inventory(
        shares_detail=shares,
        liabilities_detail=liabilities,
    )

    assert len(inventory) == 3
    assert summary.total_gap_rows == 3
    assert summary.unique_gap_tickers == 2
    assert summary.shares_gap_rows == 2
    assert summary.liabilities_gap_rows == 1
    assert summary.tickers_missing_both == 1
    assert set(inventory["field"]) == {"shares_outstanding", "total_liabilities"}


def test_gap_summary_is_field_action_classification_specific():
    inventory = pd.DataFrame(
        [
            {
                "field": "shares_outstanding",
                "ticker": "AAA",
                "recommended_action": "documented_no_supported_fact",
                "classification": "no_supported_current_share_fact",
            },
            {
                "field": "shares_outstanding",
                "ticker": "BBB",
                "recommended_action": "documented_no_supported_fact",
                "classification": "no_supported_current_share_fact",
            },
            {
                "field": "total_liabilities",
                "ticker": "AAA",
                "recommended_action": "research_alternate_tag",
                "classification": "unsupported_alternate_liability_tag_present",
            },
        ]
    )

    summary = summarize_gap_inventory(inventory)

    shares_row = summary.loc[
        summary["field"].eq("shares_outstanding")
        & summary["classification"].eq("no_supported_current_share_fact")
    ].iloc[0]
    assert shares_row["rows"] == 2
    assert shares_row["tickers"] == 2

    liabilities_row = summary.loc[
        summary["field"].eq("total_liabilities")
    ].iloc[0]
    assert liabilities_row["rows"] == 1


def test_source_candidate_template_freezes_issue26_review_fields():
    columns = list(source_candidate_template().columns)
    assert columns == [
        "source",
        "category",
        "target_fields",
        "historical_pit_support",
        "filing_or_publication_timestamp",
        "identity_mapping",
        "restatement_revision_handling",
        "methodology_documented",
        "access_cost",
        "licensing_notes",
        "sec_overlap_validation",
        "population_bias_risk",
        "fail_closed_provenance_fit",
        "research_status",
        "notes",
    ]


def test_overlap_validation_sample_is_stratified_and_deterministic():
    inventory = pd.DataFrame(
        [
            {"field": "shares_outstanding", "ticker": "DUAL1", "cik": 1, "company_name": "D1", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
            {"field": "total_liabilities", "ticker": "DUAL1", "cik": 1, "company_name": "D1", "classification": "same_context_assets_minus_equity_candidate", "recommended_action": "research_identity_candidate"},
            {"field": "shares_outstanding", "ticker": "DUAL2", "cik": 2, "company_name": "D2", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
            {"field": "total_liabilities", "ticker": "DUAL2", "cik": 2, "company_name": "D2", "classification": "unsupported_alternate_liability_tag_present", "recommended_action": "research_alternate_tag"},
            {"field": "total_liabilities", "ticker": "ALT1", "cik": 3, "company_name": "A1", "classification": "unsupported_alternate_liability_tag_present", "recommended_action": "research_alternate_tag"},
            {"field": "total_liabilities", "ticker": "ALT2", "cik": 4, "company_name": "A2", "classification": "unsupported_alternate_liability_tag_present", "recommended_action": "research_alternate_tag"},
            {"field": "total_liabilities", "ticker": "ID1", "cik": 5, "company_name": "I1", "classification": "same_context_assets_minus_equity_candidate", "recommended_action": "research_identity_candidate"},
            {"field": "total_liabilities", "ticker": "ID2", "cik": 6, "company_name": "I2", "classification": "same_context_assets_minus_equity_candidate", "recommended_action": "research_identity_candidate"},
            {"field": "shares_outstanding", "ticker": "SH1", "cik": 7, "company_name": "S1", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
            {"field": "shares_outstanding", "ticker": "SH2", "cik": 8, "company_name": "S2", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {"ticker": "CTL1", "cik": 101, "company_name": "C1", "shares_outstanding": 10, "total_liabilities": 20},
            {"ticker": "CTL2", "cik": 102, "company_name": "C2", "shares_outstanding": 11, "total_liabilities": 21},
            {"ticker": "CTL3", "cik": 103, "company_name": "C3", "shares_outstanding": 12, "total_liabilities": 22},
        ]
    )
    quotas = {
        "dual_gap": 1,
        "liabilities_alternate_tag": 1,
        "liabilities_identity_candidate": 1,
        "shares_only_gap": 1,
        "sec_supported_control": 2,
    }

    first = build_overlap_validation_sample(
        inventory=inventory,
        current_snapshot=snapshot,
        as_of="2026-09-15",
        quotas=quotas,
    )
    second = build_overlap_validation_sample(
        inventory=inventory,
        current_snapshot=snapshot,
        as_of="2026-09-15",
        quotas=quotas,
    )

    pd.testing.assert_frame_equal(first, second)
    assert first["ticker"].is_unique
    assert first["sample_cohort"].value_counts().to_dict() == {
        "sec_supported_control": 2,
        "dual_gap": 1,
        "liabilities_alternate_tag": 1,
        "liabilities_identity_candidate": 1,
        "shares_only_gap": 1,
    }


def test_overlap_validation_sample_excludes_dual_gap_from_shares_only():
    inventory = pd.DataFrame(
        [
            {"field": "shares_outstanding", "ticker": "AAA", "cik": 1, "company_name": "A", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
            {"field": "total_liabilities", "ticker": "AAA", "cik": 1, "company_name": "A", "classification": "same_context_assets_minus_equity_candidate", "recommended_action": "research_identity_candidate"},
            {"field": "shares_outstanding", "ticker": "BBB", "cik": 2, "company_name": "B", "classification": "no_supported_current_share_fact", "recommended_action": "documented_no_supported_fact"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {"ticker": "CTL", "cik": 10, "company_name": "C", "shares_outstanding": 1, "total_liabilities": 2},
        ]
    )

    sample = build_overlap_validation_sample(
        inventory=inventory,
        current_snapshot=snapshot,
        as_of="2026-09-15",
        quotas={
            "dual_gap": 1,
            "liabilities_alternate_tag": 0,
            "liabilities_identity_candidate": 0,
            "shares_only_gap": 1,
            "sec_supported_control": 0,
        },
    )

    by_cohort = dict(zip(sample["sample_cohort"], sample["ticker"]))
    assert by_cohort["dual_gap"] == "AAA"
    assert by_cohort["shares_only_gap"] == "BBB"
