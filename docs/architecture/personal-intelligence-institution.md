# Personal Intelligence Institution (Draft)

Status: Draft
Effective Date: 2026-05-30

## Purpose

Define an institution that helps the operator understand long-horizon cognitive and execution patterns through governed telemetry, reflective analysis, and replay evidence.

This draft introduces no runtime behavior changes.

## Strategic Principle

Target relationship:
- AI learns operator patterns.
- AI reflects operator patterns.
- Operator learns from reflected patterns.

The institution is designed for awareness and calibration, not control.

## Core Outputs

Personal Intelligence produces pattern-level insights, not identity claims.

Examples:
- prioritization drift trends,
- execution completion trends,
- initiative load versus completion outcomes,
- design versus execution time allocation,
- historical decision consistency versus stated goals.

## Scope

In scope:
- behavioral telemetry summaries from existing mission, agenda, intent, and workflow logs,
- trend and pattern extraction with confidence scoring,
- replay-linked explanatory evidence,
- operator-facing reflective dashboards and periodic reviews.

Out of scope:
- autonomous override of operator decisions,
- psychological diagnosis,
- identity replacement or imitation,
- hidden scoring without replay evidence,
- direct governance bypass.

## Suggested Models

Institution-level model surfaces may include:
- decision model,
- learning model,
- motivation model,
- communication model,
- goal model.

Each model must be evidence-backed and replay-traceable.

## Executive Interface Contract (Institution-Level)

Executive asks:
- What stable patterns, blind spots, and strengths are visible in recent operator behavior under current mission context?

Personal Intelligence responds with a governed schema:
- pattern_id,
- category,
- confidence,
- evidence_window,
- trend_direction,
- impact_estimate,
- supporting_replay_refs,
- recommended_adjustment,
- uncertainty_notes.

## Example Reflective Telemetry

Potential dashboard fields:
- strengths,
- growth areas,
- focus drift percentage,
- execution completion rate,
- context-switch intensity,
- delegation timing quality.

All fields must include evidence windows and replay references.

## Governance and Safety Constraints

Non-negotiable constraints:
1. Personal Intelligence cannot issue binding commands.
2. Personal Intelligence cannot mutate protected state directly.
3. Personal Intelligence must expose supporting evidence for each claim.
4. Personal Intelligence must surface uncertainty and counter-signals.
5. Operator agency remains primary; insights are advisory.

## Cross-Institution Collaboration

Expected collaboration flow:
- Executive: requests reflective insight and approves adjustments.
- Academy: contributes research methods for pattern detection quality.
- Workshop: builds instrumentation and dashboard surfaces.
- Sentinel: monitors misuse or overreach of personal telemetry.
- Identity: enforces access boundaries around sensitive personal insights.

## Suggested Next Freeze Boundary

Create a narrow contract freeze for "Personal Intelligence Reflective Telemetry Schema" before implementation.

Candidate gates:
1. Required evidence references for every high-impact insight.
2. Required uncertainty disclosure fields.
3. Replay compatibility for insight generation windows.
4. Explicit prohibition of binding decision authority.
