import pandas as pd

from finance.research.v3_data_sources import (
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
