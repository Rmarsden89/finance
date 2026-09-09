import unittest
from datetime import date, datetime
import pandas as pd
from finance.data.sec_canonical import build_canonical_facts


class ShareFallbackTests(unittest.TestCase):
    def run_case(self, changes=None, extra=None, presentation=True, enabled=True):
        sub = pd.DataFrame([dict(adsh='a', cik=1, form='10-Q', fy=2025, fp='Q2',
                                period_date=date(2025,4,30), accepted_at=datetime(2025,5,22,16,2))])
        row = dict(adsh='a', tag='CommonStockSharesOutstanding', version='us-gaap/2025',
                   value=802e6, qtrs=0, uom='shares', ddate_date=date(2025,4,30),
                   coreg='', segments='EquityComponents=CommonStock;')
        row.update(changes or {})
        rows = [row] + ([dict(row, **extra)] if extra else [])
        pre = pd.DataFrame([dict(adsh='a', tag='CommonStockSharesOutstanding', version='us-gaap/2025', stmt='EQ')]) if presentation else None
        return build_canonical_facts(sub, pd.DataFrame(rows), pre, allow_equity_component_shares=enabled)[0]

    def test_default_is_unchanged(self):
        self.assertTrue(self.run_case(enabled=False).empty)

    def test_exact_candidate_and_provenance(self):
        result = self.run_case()
        self.assertEqual(result.iloc[0].value, 802e6)
        self.assertEqual(result.iloc[0].segments, 'EquityComponents=CommonStock;')
        self.assertEqual(result.iloc[0].accepted_at, datetime(2025,5,22,16,2))

    def test_rejections(self):
        for changes in [dict(value=0), dict(value=-1), dict(value=float('inf')),
                        dict(value=float('nan')), dict(uom='USD'), dict(coreg='subsidiary'),
                        dict(qtrs=1), dict(ddate_date=date(2024,4,30)),
                        dict(segments='EquityComponents=CommonStock;Class=A;'),
                        dict(tag='WeightedAverageNumberOfSharesOutstandingBasic')]:
            with self.subTest(changes=changes):
                self.assertTrue(self.run_case(changes).empty)

    def test_multiple_classes_block_whole_filing(self):
        self.assertTrue(self.run_case(extra=dict(segments='Class=B;', value=10e6)).empty)

    def test_conflicting_values_block(self):
        self.assertTrue(self.run_case(extra=dict(value=801e6)).empty)

    def test_missing_presentation_blocks(self):
        self.assertTrue(self.run_case(presentation=False).empty)

    def test_direct_eligible_total_wins(self):
        # Standalone candidate helper receives an already-qualified total.
        from finance.data.sec_share_fallback import equity_component_share_candidates
        candidate = self.run_case()
        pre = pd.DataFrame([dict(adsh='a', tag='CommonStockSharesOutstanding', version='us-gaap/2025', stmt='EQ')])
        self.assertTrue(equity_component_share_candidates(candidate, pre, candidate).empty)


if __name__ == '__main__':
    unittest.main()
