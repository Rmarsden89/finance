from datetime import date

from finance.data.sec_current_facts import extract_companyfacts_candidates


def test_extract_companyfacts_candidates_filters_accession_and_period() -> None:
    payload = {
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2026-04-27",
                                "end": "2026-07-26",
                                "val": 100,
                            },
                            {
                                "accn": "OLD",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q3",
                                "start": "2025-04-28",
                                "end": "2025-07-27",
                                "val": 90,
                            },
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "end": "2026-07-26",
                                "val": 500,
                            }
                        ]
                    }
                },
            }
        },
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="0001",
        cik=1,
        company_name="Example Corp",
        form="10-Q",
        report_date=date(2026, 7, 26),
        filed_date=date(2026, 8, 20),
        accepted_at="2026-08-20T16:01:46",
    )

    assert set(frame["concept"]) == {"revenue", "total_assets"}
    assert dict(zip(frame["concept"], frame["qtrs"])) == {
        "revenue": 1,
        "total_assets": 0,
    }
    assert audit.rows_matching_accession == 2
    assert audit.rows_output == 2


def test_ytd_cash_flow_uses_fp_qtrs() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 123,
                            }
                        ]
                    }
                }
            }
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    assert frame.iloc[0]["concept"] == "operating_cash_flow"
    assert frame.iloc[0]["qtrs"] == 2



def test_quarterly_income_rejects_ytd_duration() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2026-04-27",
                                "end": "2026-07-26",
                                "val": 100,
                            },
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q3",
                                "start": "2025-10-27",
                                "end": "2026-07-26",
                                "val": 300,
                            },
                        ]
                    }
                }
            }
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 7, 26),
        filed_date=date(2026, 8, 20),
        accepted_at="2026-08-20T10:00:00",
    )

    assert len(frame) == 1
    assert frame.iloc[0]["value"] == 100
    assert frame.iloc[0]["qtrs"] == 1
    assert frame.iloc[0]["duration_days"] == 91


def test_dei_shares_are_used_only_as_an_exact_filing_fallback() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 500,
                            }
                        ]
                    }
                }
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "OLD",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 90,
                            },
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-03-31",
                                "val": 95,
                            },
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 100,
                            },
                        ]
                    }
                }
            },
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    shares = frame.loc[frame["concept"].eq("shares_outstanding")]
    assert len(shares) == 1
    assert shares.iloc[0]["value"] == 100
    assert shares.iloc[0]["taxonomy"] == "dei"
    assert (
        shares.iloc[0]["namespace_selection_reason"]
        == "fallback_missing_us_gaap_shares"
    )


def test_us_gaap_shares_prevent_dei_fallback() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 100,
                            }
                        ]
                    }
                }
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 110,
                            }
                        ]
                    }
                }
            },
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    shares = frame.loc[frame["concept"].eq("shares_outstanding")]
    assert len(shares) == 1
    assert shares.iloc[0]["value"] == 100
    assert shares.iloc[0]["taxonomy"] == "us-gaap"
    assert shares.iloc[0]["namespace_selection_reason"] == "primary_us_gaap"


def test_unusable_us_gaap_shares_do_not_block_dei_fallback() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 0,
                            }
                        ]
                    }
                }
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 110,
                            }
                        ]
                    }
                }
            },
        }
    }

    frame, _ = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    shares = frame.loc[frame["concept"].eq("shares_outstanding")]
    assert len(shares) == 1
    assert shares.iloc[0]["value"] == 110
    assert shares.iloc[0]["taxonomy"] == "dei"


def test_dei_fallback_rejects_nonpositive_nonshare_and_duration_rows() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 0,
                            },
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 100,
                            },
                        ],
                        "USD": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-06-30",
                                "val": 100,
                            }
                        ],
                    }
                }
            }
        }
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    assert frame.empty
    assert audit.rows_output == 0


def test_single_bounded_dei_cover_date_is_used_after_exact_fallbacks_fail() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "accn": "A",
                                "form": "10-Q",
                                "end": "2026-07-15",
                                "val": 123,
                            }
                        ]
                    }
                }
            }
        }
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    shares = frame.loc[frame["concept"].eq("shares_outstanding")]
    assert len(shares) == 1
    assert shares.iloc[0]["value"] == 123
    assert shares.iloc[0]["ddate_date"] == "2026-07-15"
    assert shares.iloc[0]["period_date"] == "2026-06-30"
    assert shares.iloc[0]["namespace_selection_reason"] == "fallback_dei_cover_date"
    assert shares.iloc[0]["date_selection_reason"] == "bounded_cover_date"
    assert audit.bounded_share_candidates_seen == 1
    assert audit.bounded_share_candidates_selected == 1
    assert audit.source_rows_seen == 1
    assert audit.rows_matching_accession == 1


def test_exact_dei_share_prevents_cover_date_fallback() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"accn": "A", "end": "2026-06-30", "val": 100},
                            {"accn": "A", "end": "2026-07-15", "val": 110},
                        ]
                    }
                }
            }
        }
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    shares = frame.loc[frame["concept"].eq("shares_outstanding")]
    assert len(shares) == 1
    assert shares.iloc[0]["value"] == 100
    assert shares.iloc[0]["date_selection_reason"] == "exact_report_date"
    assert audit.bounded_share_candidates_seen == 0


def test_bounded_cover_fallback_rejects_invalid_observations() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"accn": "OLD", "end": "2026-07-15", "val": 100},
                            {"accn": "A", "end": "2026-06-29", "val": 100},
                            {"accn": "A", "end": "2026-07-21", "val": 100},
                            {
                                "accn": "A",
                                "start": "2026-07-01",
                                "end": "2026-07-15",
                                "val": 100,
                            },
                            {"accn": "A", "end": "2026-07-15", "val": 0},
                        ],
                        "USD": [
                            {"accn": "A", "end": "2026-07-15", "val": 100}
                        ],
                    }
                }
            }
        }
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="2026-07-20T10:00:00",
    )

    assert frame.empty
    assert audit.bounded_share_candidates_seen == 0
    assert audit.bounded_share_candidates_selected == 0


def test_bounded_cover_fallback_fails_closed_for_multiple_candidates() -> None:
    for values in ([100, 100], [100, 101]):
        payload = {
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {
                                    "accn": "A",
                                    "end": f"2026-07-{15 + index:02d}",
                                    "val": value,
                                }
                                for index, value in enumerate(values)
                            ]
                        }
                    }
                }
            }
        }

        frame, audit = extract_companyfacts_candidates(
            payload,
            accession="A",
            cik=1,
            company_name="Example",
            form="10-Q",
            report_date=date(2026, 6, 30),
            filed_date=date(2026, 7, 20),
            accepted_at="2026-07-20T10:00:00",
        )

        assert frame.empty
        assert audit.bounded_share_candidates_seen == 2
        assert audit.bounded_share_candidates_selected == 0


def test_bounded_cover_fallback_requires_valid_acceptance_timestamp() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"accn": "A", "end": "2026-07-15", "val": 100}
                        ]
                    }
                }
            }
        }
    }

    frame, audit = extract_companyfacts_candidates(
        payload,
        accession="A",
        cik=1,
        company_name="Example",
        form="10-Q",
        report_date=date(2026, 6, 30),
        filed_date=date(2026, 7, 20),
        accepted_at="",
    )

    assert frame.empty
    assert audit.bounded_share_candidates_seen == 0
