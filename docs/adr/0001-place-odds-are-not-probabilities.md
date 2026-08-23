# ADR 0001: Treat place inverse odds as break-even rates, not probabilities

- Status: Accepted
- Date: 2026-08-15

## Context

JRA place betting has multiple winning horses, a payout allocation formula that differs from win betting, and a displayed odds range. Normalizing reciprocal place odds like win odds does not yield a defensible probability distribution.

## Decision

The initial product labels `[1 / upper odds, 1 / lower odds]` as the **place break-even hit-rate range**. It does not use that range as a predicted place probability, calculate place expected value from it, generate place candidates from it, or score it with Brier loss.

Place expected value and candidates remain supported by the architecture but require a later independent, time-valid place-probability model.

## Consequences

- Win odds can supply a normalized market-share baseline; place odds cannot use the same normalization.
- The initial release may settle and analyze real place bets, but its own place candidate list is empty.
- Literature-derived context conditions remain disabled, multiplier-free analysis tags until prospective data supports a probability adjustment.
- The decision avoids a circular calculation in which the same odds provide both the alleged probability and the price being evaluated.

## Evidence

See the [primary-source research report on initial prediction rules](../research/initial-prediction-rules.md). JRA's official payout formula and place-bet definition are the controlling product-rule sources.
