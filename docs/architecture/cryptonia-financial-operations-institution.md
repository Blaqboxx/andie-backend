# Cryptonia Financial Operations Institution (Draft)

Status: Draft
Effective Date: 2026-05-30

## Purpose

Reframe Cryptonia from a crypto-only component into a Financial Operations Institution that evaluates opportunities across multiple asset classes under Executive governance.

This draft introduces no runtime behavior changes.

## Strategic Reframe

Old framing:
- Cryptonia = Crypto Division.

New framing:
- Cryptonia = Financial Operations Institution.

The institution objective is not asset-class loyalty. The objective is governed capital allocation quality.

## Mission Alignment

If mission intent is financial stability, institution output must optimize for:
- risk-adjusted return,
- liquidity,
- volatility tolerance,
- capital preservation,
- growth horizon fit,
- portfolio resilience.

## Asset Universe (Initial)

Cryptonia should support policy-governed analysis across:
- Cryptocurrency,
- Equities,
- ETFs,
- Commodities,
- Forex,
- Treasury and yield products,
- Alternative assets.

## Internal Functional Divisions

### Market Intelligence
- market scanning,
- macro and event context,
- news and sentiment synthesis,
- market structure signals.

### Strategy Division
- momentum,
- mean reversion,
- long-horizon growth,
- income and yield,
- arbitrage (where policy-permitted).

### Risk Division
- position sizing,
- drawdown control,
- exposure limits,
- stress scenarios.

### Portfolio Division
- allocation,
- diversification,
- rebalancing,
- policy-constrained capital rotation.

## Executive Interface Contract (Institution-Level)

Executive asks:
- What opportunities exist now under current policy and budget constraints?

Cryptonia responds with ranked opportunities using a governed schema:
- asset,
- asset_class,
- confidence,
- risk_level,
- expected_horizon,
- liquidity_profile,
- rationale,
- policy_constraints_checked,
- suggested_allocation_bounds.

## Cross-Institution Collaboration

Expected institution flow:
- Academy: market and structural research inputs.
- Cryptonia: opportunity and allocation analysis.
- Workshop: strategy and tooling implementation.
- Sentinel: risk and anomaly monitoring.
- Executive: final governance-gated capital decisions.

## Governance and Safety Constraints

Non-negotiable constraints:
1. Cryptonia does not bypass Executive governance.
2. Cryptonia does not bypass identity checks.
3. Cryptonia does not directly mutate protected world state.
4. Allocation actions require policy and budget gates.
5. All opportunity recommendations are auditable and replayable.

## Out of Scope (This Draft)

- Broker-specific integrations.
- Order execution implementation.
- Live multi-asset portfolio engine rollout.
- New autonomous authority for Cryptonia.

## Suggested Next Freeze Boundary

Create a narrow contract freeze for "Cryptonia Multi-Asset Opportunity Schema" before implementation.

Candidate gates:
1. Schema conformance for opportunity ranking output.
2. Risk and liquidity fields required for every recommendation.
3. Replay compatibility for recommendation sessions.
4. No governance bypass across institution interactions.
