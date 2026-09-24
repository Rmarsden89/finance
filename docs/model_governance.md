# Model Governance

## Champion/challenger model

The live or shadow system uses one frozen **champion** model version. New model versions are **challengers** until they satisfy the agreed validation criteria.

## Promotion rule

A challenger is not promoted because of one successful live recommendation. Promotion must be supported by walk-forward evidence across multiple historical market regimes and benchmark-relative evaluation.

## Live observations

Low-dollar live results are intentionally useful for learning about operational behavior, drawdowns, confidence, and risk tolerance. They may generate hypotheses for investigation.

They do **not** directly retrain or retune the model.

The loop is:

1. Observe live behavior.
2. Form a testable hypothesis.
3. Test the hypothesis using point-in-time historical data and walk-forward validation.
4. Accept or reject the proposed model change based on evidence.
5. Create a new immutable model version if warranted.
6. Promote only after the challenger meets the promotion criteria.

## Initial live constraint

Maximum new live capital allocation: **$10 per week**.


## Model-version isolation on main

Model versions are isolated by immutable model identifiers, configuration,
artifact namespaces, and runtime capabilities rather than by permanently
separate Git branches.

The intended steady state is:

- `long_growth_v1` remains the live champion;
- `long_growth_v2_ttm_valuation_v1` remains a research-only challenger until
  a separate explicit promotion decision;
- both versions may exist on `main` so the weekly shadow observation can use
  the same point-in-time inputs without branch switching;
- only the V1 live workflow may reach broker review, order-intent, placement,
  modification, or cancellation capabilities;
- V2 shadow/research commands must remain execution-inert and write only to
  their isolated artifact namespaces.

A code merge that places both models on `main` is not itself a model
promotion. Live model promotion remains a separate governance decision.
