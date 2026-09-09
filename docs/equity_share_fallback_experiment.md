# Equity-component share fallback experiment

Status: experimental; not enabled in the production/cache path.

## Why this exists

The 2025 AMAT quarterly filings report current common shares in an equity-statement fact with the dimension `EquityComponents=CommonStock;`. The existing consolidated-company filter intentionally removes all dimensional facts, so the weekly panel carries the prior annual share count until the next annual filing.

For AMAT, the experiment adds six quarterly candidates:

- 2024 Q1: 831 million
- 2024 Q2: 828 million
- 2024 Q3: 824 million
- 2025 Q1: 812 million
- 2025 Q2: 802 million
- 2025 Q3: 797 million

The raw source also contains weighted-average shares and other equity component rows. Those are excluded.

## Rule

The fallback is opt-in through `build_canonical_facts(..., allow_equity_component_shares=True)`. It considers a filing only when:

- no eligible non-dimensional shares fact exists for that filing;
- the current-period fact has `CommonStockSharesOutstanding`, `qtrs=0`, `uom=shares`, and a positive finite value;
- presentation metadata places the tag on `EQ`;
- the dimension is exactly `EquityComponents=CommonStock;`;
- all share rows in the filing have only blank or that exact dimension, no `coreg`, and one positive value;
- no conflicting value, unknown dimension, other class, stale period, or weighted-average share tag appears.

The fallback never sums classes, strips dimensions, or replaces an existing eligible total. The selected row retains its dimension and gets `share_selection_rule=experimental_eq_common_stock_v1`.

## Evidence

Using the AMAT lineage bundle:

- baseline rebuilt from raw SEC evidence matched all 74 supplied cached winners;
- the experiment changed no non-share facts;
- six share rows were added;
- 43 of 52 weekly AMAT rows changed share input;
- the experiment is not a multi-company validation and has not changed rankings, factors, or backtest results.

Run the audit from a checkout of this branch:

```powershell
py scripts\audit_equity_share_fallback.py reports\lineage\<bundle-or-path>.zip --output-dir reports\share_fallback\<run-id>
```

The audit writes added rows, weekly share impact, and a JSON summary. It does not modify caches.

## Required validation before adoption

Run this experiment across representative companies, including single-class, multiple-class, dual-class, unknown-dimension, amended-filing, and missing-presentation cases. Review candidate counts and rejection reasons. Then compare raw valuation factors, normalized scores, family scores, eligibility, and Top-10 membership on the same universe and decision dates.

Until that work passes, leave the default flag false and do not rebuild the canonical research baseline.
