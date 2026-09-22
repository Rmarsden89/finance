# V2 Research Track

## Purpose

V2 is a research-only challenger track built from the hardened V1 baseline.
The live champion remains `long_growth_v1` on `main`. V2 work is developed on
`v2-modifications` and cannot silently change V1 artifacts or broker behavior.

## Safety boundary

The first V2 model identifier is:

`long_growth_v2_research`

V2 artifacts are isolated under:

`reports/v2/long_growth_v2_research/<decision-date>/`

The V2 research configuration hard-disables:

- broker access;
- order-intent generation;
- order review;
- order placement.

The V2 entry point does not import the broker or shadow execution packages.
Any future expansion must preserve these restrictions until a separate,
explicit promotion decision changes the contract.

## Initialize a research run

From the `v2-modifications` branch:

```powershell
cd C:\Repos\finance
git switch v2-modifications
git pull

py scripts\run_v2_research.py `
  --as-of YYYY-MM-DD `
  --data-baseline-id "<immutable baseline identifier>"
```

The initialization command writes only `research_manifest.json`. It does not
score a V2 model and does not connect to Robinhood.

The manifest records:

- model ID;
- source champion;
- configuration hash;
- data-baseline identifier;
- decision date;
- creation timestamp;
- disabled execution capabilities;
- isolated artifact directory.

## V1 remains separate

V1 live operations continue from `main` using
`docs/long_growth_v1_live_runbook.md`. Never run the V1 live workflow from the
V2 branch.

Factor, family, coverage, and allocation experiments are added to V2 only
through their tracked GitHub issues and must carry their own validation
artifacts.

## Build the isolated V2 SEC challenger

The frozen V1 SEC candidate builder defaults to exact-date US-GAAP behavior.
The approved DEI exact-date and bounded cover-date fallbacks require the named
`v2_dei_cover_date` research policy and may write only beneath the dated V2 run
directory.

Build the V2 SEC shadow history and current panel from existing local inputs:

```powershell
py scripts\run_v2_sec_research.py `
  --as-of YYYY-MM-DD `
  --data-baseline-id "<immutable baseline identifier>" `
  --pitindex-data "C:\Repos\pitindex\pitindex\data" `
  --market-snapshot "reports\shadow\YYYY-MM-DD\robinhood_market_snapshot_normalized.csv"
```

The command reads the existing discovery, CompanyFacts cache, historical SEC
winners, historical research panel, PITIndex data, and normalized saved market
snapshot. It does not refresh SEC data or contact Robinhood.

All generated candidates, merge audits, shadow winners, scoring panel, current
snapshot, and manifest are written under:

`reports/v2/long_growth_v2_research/<decision-date>/`

The command cannot create order intents, review orders, or place orders. The V1
candidate reports and `data/cache/sec/shadow` are not modified.

V2 also treats zero or negative historical `shares_outstanding` winners as
missing rather than valid share counts. The original rows and provenance are
retained in `sec_share_quality_adjustments.csv`; only the V2 shadow copy is
normalized. This prevents an invalid zero from becoming the current winner
without falling back to an older positive value.

## Compare the frozen V1 baseline with the V2 challenger

After the isolated SEC run reaches `SEC_RESEARCH_COMPLETE`, build a same-input
comparison using only saved SEC, PITIndex, market, historical-panel, and V1
decision artifacts:

```powershell
py scripts\run_v2_impact_comparison.py `
  --as-of YYYY-MM-DD `
  --pitindex-data "C:\Repos\pitindex\pitindex\data"
```

The command rebuilds an exact-only V1 baseline beneath the V2 run directory,
scores both panels with the frozen `long_growth_v1` factor and scoring stack,
and compares shares coverage, Valuation availability, top-conviction
eligibility, ranks, and the ordered Top 10. It also verifies the baseline Top
10 and scores against the saved V1 `shadow_decision.json` and audits share
filing/acceptance dates for point-in-time violations.

Comparison artifacts are written only under:

`reports/v2/long_growth_v2_research/<decision-date>/impact/`

This command reads the saved V1 decision artifact but does not import execution
modules, create order intents, or contact Robinhood.

## Immutable input fingerprints

Every completed V2 SEC research run now records SHA-256 fingerprints for the
exact discovery CSV, referenced CompanyFacts JSON files, historical SEC winner
file, historical panel, three PITIndex CSVs, normalized market snapshot, Git
commit, and tracked-worktree cleanliness. The full member list is stored in

`input_fingerprints.json`

The impact comparison recomputes these fingerprints and fails closed if any
input or the code commit differs. Run research commands from a clean tracked
worktree; ignored data/report artifacts do not make the worktree dirty.

## Audit and recover residual shares gaps

Classify every blank or nonpositive V2 shares value:

```powershell
py scripts\audit_v2_residual_shares.py `
  --as-of YYYY-MM-DD
```

The audit distinguishes targeted SEC discovery/cache gaps, invalid zero or
negative values, candidate-selection defects, and companies with no supported
current fact. It does not fetch or promote data.

If targeted SEC gaps exist, set `SEC_USER_AGENT` and retry only those tickers:

```powershell
$env:SEC_USER_AGENT="Your Name your-email@example.com"

py scripts\refresh_v2_share_gaps.py `
  --as-of YYYY-MM-DD `
  --pitindex-data "C:\Repos\pitindex\pitindex\data"
```

The refresh writes a merged discovery copy beneath the V2 cleanup directory
and prints the isolated `run_v2_sec_research.py` command needed to rebuild the
challenger. It does not modify V1 reports or winner caches and has no broker or
order capability.
