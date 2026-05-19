"""
STEP 13B — Policy Engine
=========================
Stores, matches, and evaluates governance policies against task execution requests.

This is ANDIE's "operational law" — the set of rules that govern what it is and
is not permitted to do autonomously.

Policy resolution
-----------------
When multiple policies match, the engine applies the **most restrictive action**
(highest ``PolicyAction.severity``).  Ties are broken by ``priority`` (higher
priority wins), then by policy name (alphabetical, deterministic).

This means:
    - A BLOCK policy always overrides ALLOW
    - A REQUIRE_HUMAN overrides REQUIRE_CONSENSUS
    - Among equal actions, the higher-priority policy's metadata is used

Built-in default policies
--------------------------
The engine ships with three default policies that reflect sensible safety
defaults.  They can be overridden or disabled by registering custom policies
with higher priority and the same match criteria.

    "default_high_risk"     — REQUIRE_CONSENSUS when risk > 0.60
    "default_critical_risk" — REQUIRE_HUMAN when risk > 0.80
    "default_blocked"       — BLOCK when risk > 0.92

Usage
-----
    engine = PolicyEngine()

    # Add a project-specific policy
    engine.register(GovernancePolicy(
        name="no_prod_deploy_after_hours",
        match_tasks=["deploy_prod"],
        risk_threshold=0.0,
        action=PolicyAction.REQUIRE_HUMAN,
        priority=10,
    ))

    match = engine.evaluate("deploy_prod",
                             tags=["prod", "destructive"],
                             risk=0.65,
                             operation="deploy")
    print(match.action)          # PolicyAction.REQUIRE_HUMAN
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .governance_models import (
    GovernancePolicy, PolicyAction, PolicyMatch,
)


# ── Built-in default policies ─────────────────────────────────────────────────

_DEFAULT_POLICIES: List[GovernancePolicy] = [
    GovernancePolicy(
        name="default_allow",
        description="Baseline: allow any task with low risk",
        risk_threshold=0.0,
        max_allowed_risk=0.59,
        action=PolicyAction.ALLOW,
        priority=0,
    ),
    GovernancePolicy(
        name="default_high_risk",
        description="Require consensus when risk exceeds 60%",
        risk_threshold=0.60,
        max_allowed_risk=0.79,
        action=PolicyAction.REQUIRE_CONSENSUS,
        priority=0,
    ),
    GovernancePolicy(
        name="default_critical_risk",
        description="Require human approval when risk exceeds 80%",
        risk_threshold=0.80,
        max_allowed_risk=0.91,
        action=PolicyAction.REQUIRE_HUMAN,
        priority=0,
    ),
    GovernancePolicy(
        name="default_blocked",
        description="Block execution when risk exceeds 92%",
        risk_threshold=0.92,
        max_allowed_risk=1.0,
        action=PolicyAction.BLOCK,
        priority=0,
    ),
]

_DESTRUCTIVE_KEYWORDS = (
    "delete", "drop", "destroy", "wipe", "purge", "rm_rf",
    "reset_prod", "truncate", "remove_all",
)


class PolicyEngine:
    """Stores governance policies and evaluates them against task requests.

    Parameters
    ----------
    include_defaults:
        If True (default), load the four built-in risk-threshold policies.
    """

    def __init__(self, include_defaults: bool = True) -> None:
        self._policies: Dict[str, GovernancePolicy] = {}
        if include_defaults:
            for p in _DEFAULT_POLICIES:
                self._policies[p.name] = p

    # ── Policy management ─────────────────────────────────────────────────────

    def register(self, policy: GovernancePolicy) -> None:
        """Add or replace a policy by name."""
        self._policies[policy.name] = policy

    def unregister(self, name: str) -> bool:
        """Remove a policy.  Returns True if it existed."""
        return self._policies.pop(name, None) is not None

    def disable(self, name: str) -> bool:
        if name in self._policies:
            self._policies[name] = self._policies[name].model_copy(
                update={"enabled": False}
            )
            return True
        return False

    def enable(self, name: str) -> bool:
        if name in self._policies:
            self._policies[name] = self._policies[name].model_copy(
                update={"enabled": True}
            )
            return True
        return False

    def get(self, name: str) -> Optional[GovernancePolicy]:
        return self._policies.get(name)

    def all_policies(self) -> List[GovernancePolicy]:
        return list(self._policies.values())

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        task:      str,
        tags:      List[str],
        risk:      float,
        operation: str = "",
    ) -> PolicyMatch:
        """Return the most restrictive policy match for this task.

        Always returns a ``PolicyMatch`` — falls back to the built-in defaults
        even if no custom policies are registered.
        """
        # Auto-tag destructive operations
        if any(k in (task + operation).lower() for k in _DESTRUCTIVE_KEYWORDS):
            tags = list(tags) + ["destructive"]

        matches: List[tuple[GovernancePolicy, PolicyMatch]] = []
        for policy in self._policies.values():
            if policy.matches(task, tags, risk, operation):
                pm = PolicyMatch(
                    policy_name=policy.name,
                    action=policy.action,
                    priority=policy.priority,
                    rationale=f"policy='{policy.name}' risk={risk:.3f}",
                )
                matches.append((policy, pm))

        if not matches:
            # Fallback: ALLOW with zero priority
            return PolicyMatch(
                policy_name="fallback_allow",
                action=PolicyAction.ALLOW,
                priority=-1,
                rationale="No policies matched; defaulting to ALLOW",
            )

        # Most restrictive action wins; break ties by priority desc, then name
        matches.sort(
            key=lambda t: (t[0].action.severity, t[0].priority, t[0].name),
            reverse=True,
        )
        _, winning_match = matches[0]
        return winning_match

    def evaluate_all(
        self,
        task:      str,
        tags:      List[str],
        risk:      float,
        operation: str = "",
    ) -> List[PolicyMatch]:
        """Return ALL matching policy matches (sorted most-to-least restrictive)."""
        if any(k in (task + operation).lower() for k in _DESTRUCTIVE_KEYWORDS):
            tags = list(tags) + ["destructive"]

        results: List[PolicyMatch] = []
        for policy in self._policies.values():
            if policy.matches(task, tags, risk, operation):
                results.append(PolicyMatch(
                    policy_name=policy.name,
                    action=policy.action,
                    priority=policy.priority,
                    rationale=f"policy='{policy.name}' risk={risk:.3f}",
                ))

        results.sort(
            key=lambda m: (m.action.severity, m.priority),
            reverse=True,
        )
        return results

    def is_blocked(
        self,
        task:      str,
        tags:      List[str],
        risk:      float,
        operation: str = "",
    ) -> bool:
        """Quick check — is this task unconditionally blocked?"""
        return self.evaluate(task, tags, risk, operation).action == PolicyAction.BLOCK

    def __len__(self) -> int:
        return len(self._policies)

    def __contains__(self, name: str) -> bool:
        return name in self._policies
