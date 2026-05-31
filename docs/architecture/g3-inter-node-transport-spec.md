# G3.3 Inter-Node A2A Transport Specification (Frozen)

Status: Frozen
Effective Date: 2026-05-30
Milestone Tag: valhalla-g3-inter-node-transport-spec

## Purpose

Define inter-node transport behavior for A2A exchanges without changing local workflow semantics established in G3.2.

## Design Rule

G3.2 changes who can collaborate.
G3.3 changes where they are located.

The workflow contract, message lifecycle, and governance guarantees remain unchanged.

## Scope

In scope:
- Host-to-host transport between verified Valhalla nodes.
- Delivery envelope carriage for existing A2A fields.
- Transport retry and timeout policy.
- Transport-level audit records.

Out of scope:
- New workflow semantics.
- New institution authority.
- Direct world-state mutation permissions.
- Bypassing Executive, Governance, or Identity controls.

## Verified Node Topology

- blaqtower2: core executive runtime.
- blaqtower (alias Blaqtower1 / NUC 1): institution services.
- blaqtower3 (alias Blaqtower3 / GPU PC): inference services.

## Non-Negotiable Constraints

1. Inter-node transport must preserve existing A2A envelope fields and meanings.
2. session_id and correlation_id must remain stable end-to-end.
3. Sender and receiver identities must be validated before send and before accept.
4. Governance policy evaluation remains mandatory.
5. Transport layer must not grant mutation authority.
6. Every transport event must be auditable.

## Required Envelope Continuity

The following fields are mandatory across node boundaries:
- message_id
- correlation_id
- session_id
- sender
- receiver
- message_type
- request
- response
- status
- created_at
- updated_at
- timeout_seconds
- error_code
- error_message
- policy_decision_id (when present)
- intent_id (when present)

## Transport Semantics

1. Transport is at-least-once delivery with idempotent message handling.
2. Duplicate message_id receipt must be handled as replay-safe and non-mutating.
3. Delivery acknowledgement is transport-level and separate from business response.
4. Business response continues to use existing A2A responded/rejected/timed_out states.

## Timeout and Retry

1. Transport retries must be bounded and policy-configurable.
2. Retry events must be audited with attempt count and node endpoints.
3. Exhausted retries transition to deterministic failure with audit evidence.
4. Transport timeout must not alter workflow semantics beyond existing A2A state machine.

## Failure Classes (Transport)

- node_unreachable
- transport_timeout
- authentication_failure
- authorization_failure
- duplicate_delivery
- delivery_rejected
- retry_exhausted

All failure classes require auditable persistence.

## Audit Requirements

Transport audit records must include:
- source_node
- destination_node
- message_id
- correlation_id
- session_id
- sender
- receiver
- transport_status
- attempt
- timestamp
- error_code (if any)
- error_message (if any)

Replay queries must be able to reconstruct:
- local workflow sequence
- cross-node transport attempts
- final workflow outcome

## Security and Identity

1. Node identity must be authenticated for each transport session.
2. Institution identity must remain distinct from node identity.
3. No trust escalation from transport authentication to governance bypass.
4. Unauthorized nodes or senders must be rejected and audited.

## Compatibility Requirement

A workflow executed locally and the same workflow executed across nodes must produce equivalent semantic outcomes for:
- status transitions
- correlation chain
- replay output shape
- governance outcomes

## Exit Criteria For G3.3

1. Inter-node transport preserves envelope continuity.
2. Retry and timeout behavior is deterministic and audited.
3. Duplicate deliveries are idempotent and replay-safe.
4. Governance and identity checks remain enforced end-to-end.
5. Replay can reconstruct full cross-node workflow history.

## Change Control

Any semantic change to A2A workflow behavior requires:
1. update to G3.0/G3.2 contracts,
2. conformance test updates,
3. a new freeze tag.
