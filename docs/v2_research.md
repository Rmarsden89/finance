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

The command writes only `research_manifest.json`. It does not score a V2 model
yet and does not connect to Robinhood.

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
