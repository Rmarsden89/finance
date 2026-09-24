from __future__ import annotations

import pandas as pd

from finance.research.ttm_q4_difference import (
    characterize_q4_material_differences,
    repeated_q4_difference_ciks,
    summarize_q4_difference_dimension,
)


def test_characterize_q4_material_differences_classifies_patterns() -> None:
    frame = pd.DataFrame([
        {
            "cik": 1,
            "concept": "revenue",
            "ttm_end_fy": 2025,
            "ttm_value": 120.0,
            "reported_annual_value": 100.0,
            "relative_difference_abs": 0.20,
            "material_difference_gt_1pct": True,
            "quarter_derivations": (
                "direct_qtrs_1|direct_qtrs_1|direct_qtrs_1|"
                "annual_minus_q3_ytd"
            ),
            "source_tags": "Revenues||Revenues||SalesRevenueNet||Revenues",
            "source_forms": "10-Q||10-Q||10-Q/A||10-K",
            "source_adshs": "a1||a2||a3||a4",
            "reported_annual_source_tag": "Revenues",
        }
    ])

    detail = characterize_q4_material_differences(frame)

    row = detail.iloc[0]
    assert row["difference_magnitude_band"] == "5-20pct"
    assert row["derivation_pattern"] == "q2_q3_both_direct"
    assert row["source_tag_continuity"] == "multiple_tags"
    assert bool(row["amendment_present"])
    assert row["review_category"] == "amendment_present"


def test_repeated_q4_difference_ciks_requires_multiple_rows() -> None:
    detail = pd.DataFrame([
        {
            "concept": "net_income",
            "cik": 1,
            "ttm_end_fy": 2024,
            "relative_difference_abs": 0.10,
        },
        {
            "concept": "net_income",
            "cik": 1,
            "ttm_end_fy": 2025,
            "relative_difference_abs": 0.20,
        },
        {
            "concept": "revenue",
            "cik": 2,
            "ttm_end_fy": 2025,
            "relative_difference_abs": 0.30,
        },
    ])

    repeated = repeated_q4_difference_ciks(detail)

    assert len(repeated) == 1
    assert repeated.iloc[0]["cik"] == 1
    assert repeated.iloc[0]["rows"] == 2
    assert repeated.iloc[0]["fiscal_years"] == "2024|2025"


def test_summarize_q4_difference_dimension_counts_rows_and_ciks() -> None:
    detail = pd.DataFrame([
        {
            "concept": "revenue",
            "cik": 1,
            "difference_magnitude_band": "1-5pct",
            "relative_difference_abs": 0.02,
        },
        {
            "concept": "revenue",
            "cik": 2,
            "difference_magnitude_band": "1-5pct",
            "relative_difference_abs": 0.03,
        },
    ])

    summary = summarize_q4_difference_dimension(
        detail, "difference_magnitude_band"
    )

    assert len(summary) == 1
    assert summary.iloc[0]["rows"] == 2
    assert summary.iloc[0]["unique_ciks"] == 2
