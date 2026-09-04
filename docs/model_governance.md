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
