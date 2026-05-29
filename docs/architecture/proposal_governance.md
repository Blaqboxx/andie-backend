# Proposal Governance Architecture

## Chain of Authority
Institution -> Proposal -> Identity Review -> Executive Review -> Execution -> World Mutation -> Audit Record

## Enforcement
- Direct mutation path is denied.
- Only approved proposals can execute.
- Rejected proposals are non-executable.
- Execution requires identity authorization.

## Auditing
Cycle audits capture proposal volume, approval/rejection outcomes, and budget rollback conditions.
