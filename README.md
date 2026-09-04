# Finance Research

A versioned research project for developing and validating a long-term investing model before and alongside limited live deployment.

## Current focus

1. Point-in-time historical data
2. Walk-forward validation
3. Versioned scoring models
4. Benchmark comparison
5. Risk and allocation rules
6. Live shadow / low-dollar validation
7. Brokerage execution only after the research path is stable

## Core principle

Live results may generate hypotheses, but model changes must be validated against historical walk-forward data before promotion.

## Initial operating constraints

- Long-term investing, not day trading
- Weekly decision cadence
- Initial live capital capped at $10/week
- No leverage, margin, options, or forced trades
- Broad-market benchmark
- Model versions are immutable after evaluation
- Brokerage integration is isolated from the research engine

## Planned layout

```text
src/finance/
  config.py
  models/
  data/
  backtest/
  portfolio/
  risk/

tests/
docs/
```

## Development stages

### Stage 1 — Research framework
Build deterministic scoring, portfolio accounting, and walk-forward evaluation.

### Stage 2 — Historical validation
Evaluate performance, drawdown, volatility, calibration, and benchmark-relative results across multiple market regimes.

### Stage 3 — Shadow mode
Generate weekly recommendations without trading.

### Stage 4 — Low-dollar live validation
Use a frozen promoted model with a maximum of $10/week while continuing to develop challenger models separately.

### Stage 5 — Execution integration
Integrate brokerage execution only after the controls, audit trail, and promotion process are established.
