# G3.0 Local A2A Specification (Frozen)

Status: Frozen
Effective Date: 2026-05-30
Milestone Tag: valhalla-g3-local-a2a-spec

## Purpose

Define the canonical local A2A contract before any inter-node transport implementation.

This specification freezes message semantics, audit behavior, timeout expectations, and failure handling for local institution-to-institution coordination.

## Scope

In scope:
- Local process message exchange between institutions.
- Request/response lifecycle tracking.
- Audit requirements and replay compatibility.
- Timeout and failure handling rules.

Out of scope:
- Inter-node networking.
- Cross-host delivery guarantees.
- Distributed consensus.
- Direct world-state mutation authority.

## Non-Negotiable Constraints

1. A2A cannot bypass executive governance.
2. A2A cannot bypass identity checks.
3. A2A cannot directly mutate world state.
4. Every exchange must be auditable end-to-end.

## Message Envelope

Required fields:
- message_id: globally unique message identifier.
- correlation_id: identifier linking related messages in the same conversational chain.
- session_id: execution session identifier for replay and audit scope.
- sender: institutional sender identity.
- receiver: institutional receiver identity.
- message_type: semantic type of request or response.
- request: sender payload.
- response: receiver payload; null until completed.
- status: pending, responded, rejected, or timed_out.
- created_at: send timestamp (UTC).
- updated_at: latest status-change timestamp (UTC).

Optional fields:
- policy_decision_id: governing decision id for traceability.
- intent_id: executive intent linkage.
- timeout_seconds: explicit override within allowed policy range.
- error_code: machine-readable failure identifier.
- error_message: human-readable failure detail.

## Identity Requirements

1. sender and receiver must be known institutional identities.
2. Sender identity must be validated before message acceptance.
3. Receiver identity must be validated before routing.
4. Unknown, suspended, or invalid identities must be rejected and audited.

## Governance Requirements

1. Governance policy evaluation is mandatory on send.
2. Mutation-oriented message types are blocked in local protocol mode.
3. Rejection events must include policy reason and be persisted.
4. Governance-denied messages must never be delivered to receiver handlers.

## Timeout Rules

1. Default timeout for pending requests: 300 seconds.
2. Per-message timeout may be set only within policy limits.
3. On timeout expiration, status transitions to timed_out.
4. Timeout transition must record updated_at and timeout metadata.
5. Timeout events must be visible in audit queries and replay.

## Failure Rules

Failure classes:
- identity_failure: sender/receiver identity invalid.
- governance_rejection: blocked by policy.
- handler_failure: receiver handler execution failed.
- timeout: no response before timeout deadline.
- persistence_failure: ledger write/read failure.

Failure handling requirements:
1. Any failure must be written to audit ledger.
2. Status must transition deterministically.
3. Partial failures must never create unaudited state.
4. System must preserve replayability even for failures.

## Audit Requirements

Every message record must preserve:
- envelope fields and status transitions.
- timestamps for creation and updates.
- decision and intent references when present.
- failure metadata when applicable.

Minimum query surfaces:
- get by message_id
- list by session_id
- list by receiver inbox
- status timeline suitable for replay

## State Machine

Allowed transitions:
- pending -> responded
- pending -> rejected
- pending -> timed_out

Disallowed transitions:
- responded -> pending
- rejected -> pending
- timed_out -> pending

Once terminal, a message is immutable except for audit annotations.

## Implementation Notes

Current local protocol implementation is expected to map to this contract through:
- local router send/respond operations,
- durable message ledger persistence,
- API surfaces for get/list/inbox/session views.

## Exit Criteria For G3.0

1. All required envelope fields enforced.
2. Governance and identity rules enforced on send path.
3. Timeout and failure transitions implemented and audited.
4. Replay views include successful and failed exchanges.
5. Contract tests cover allowed/disallowed state transitions.

## Change Control

Changes to this document require:
1. explicit architecture update,
2. corresponding test updates,
3. a new freeze tag if semantics change.
