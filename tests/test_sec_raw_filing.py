from finance.research.sec_raw_filing import (
    accession_archive_cik,
    filing_document_url,
    filing_header_url,
    parse_inline_xbrl_evidence,
)


def test_accession_archive_cik_and_urls():
    accession = "0000123456-26-000001"
    assert accession_archive_cik(accession) == 123456
    assert filing_header_url(accession=accession).endswith(
        "/123456/000012345626000001/0000123456-26-000001.hdr.sgml"
    )
    assert filing_document_url(
        accession=accession,
        primary_document="example.htm",
    ).endswith("/123456/000012345626000001/example.htm")


def test_parse_inline_xbrl_evidence_extracts_target_facts_and_contexts():
    html = """
    <html>
      <body>
        <xbrli:context id="c1">
          <xbrli:period>
            <xbrli:instant>2026-06-30</xbrli:instant>
          </xbrli:period>
        </xbrli:context>
        <xbrli:context id="c2">
          <xbrli:entity>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="dei:LegalEntityAxis">
                dei:ParentCompanyMember
              </xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2026-01-01</xbrli:startDate>
            <xbrli:endDate>2026-06-30</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>
        <ix:nonFraction name="dei:EntityCommonStockSharesOutstanding"
                        contextRef="c1" unitRef="shares" decimals="-3">
          123,456
        </ix:nonFraction>
        <ix:nonFraction name="us-gaap:Liabilities"
                        contextRef="c1" unitRef="USD" decimals="-6">
          987,654
        </ix:nonFraction>
        <ix:nonFraction name="us-gaap:Assets"
                        contextRef="c1" unitRef="USD">111</ix:nonFraction>
      </body>
    </html>
    """
    facts, contexts = parse_inline_xbrl_evidence(html)
    assert [fact.name for fact in facts] == [
        "dei:EntityCommonStockSharesOutstanding",
        "us-gaap:Liabilities",
    ]
    assert facts[0].value_text == "123,456"
    assert contexts["c1"].instant == "2026-06-30"
    assert contexts["c2"].start_date == "2026-01-01"
    assert contexts["c2"].end_date == "2026-06-30"
    assert contexts["c2"].dimensions == "dei:ParentCompanyMember"
