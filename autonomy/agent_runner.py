from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict


RunAgentFn = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
PublishEventFn = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class AgentRunner:
    def __init__(self, run_agent: RunAgentFn, publish_event: PublishEventFn) -> None:
        self._run_agent = run_agent
        self._publish_event = publish_event
        self._attempts: Dict[str, int] = {}
        self._cooldowns: Dict[str, float] = {}

    def _policy(self, agent: Dict[str, Any]) -> Dict[str, Any]:
        policies = agent.get("policies") if isinstance(agent.get("policies"), dict) else {}
        max_retries = int(policies.get("maxRetries", 3))
        cooldown_ms = int(policies.get("cooldownMs", 10_000))
        return {"maxRetries": max(max_retries, 0), "cooldownMs": max(cooldown_ms, 0)}

    def _in_cooldown(self, agent_id: str, cooldown_ms: int) -> bool:
        last_run = self._cooldowns.get(agent_id, 0.0)
        return (time.time() - last_run) * 1000 < cooldown_ms

    async def run(
        self,
        *,
        agent: Dict[str, Any],
        context: Dict[str, Any],
        trigger_id: str,
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent_id = str(agent.get("id") or "unknown_agent")
        policy = self._policy(agent)
        event = context.get("event") if isinstance(context.get("event"), dict) else {}
        workflow_id = event.get("workflowId")
        task_id = event.get("taskId") or (event.get("task") or {}).get("id")
        plan_step = context.get("planStep") if isinstance(context.get("planStep"), dict) else None

        if self._in_cooldown(agent_id, policy["cooldownMs"]):
            result = {
                "status": "skipped",
                "agent": agent_id,
                "reason": "agent_cooldown_active",
                "triggerId": trigger_id,
                "planStep": plan_step,
            }
            await self._publish_event(
                {
                    "type": "AGENT_ACTION_SKIPPED",
                    "message": f"Agent {agent_id} skipped due to cooldown",
                    "result": result,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "metadata": {"autonomySource": "agent_runner", "triggerId": trigger_id, "planStep": plan_step},
                }
            )
            return result

        attempts = self._attempts.get(agent_id, 0)
        if attempts >= policy["maxRetries"]:
            result = {
                "status": "failed",
                "agent": agent_id,
                "reason": "max_retries_reached",
                "triggerId": trigger_id,
                "planStep": plan_step,
            }
            await self._publish_event(
                {
                    "type": "AGENT_ACTION_FAILED",
                    "message": f"Agent {agent_id} reached max retries",
                    "result": result,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "metadata": {"autonomySource": "agent_runner", "triggerId": trigger_id, "planStep": plan_step},
                }
            )
            return result

        self._attempts[agent_id] = attempts + 1
        try:
            run_input = {
                "event": context.get("event"),
                "decision": decision,
                "triggerId": trigger_id,
                "input": decision.get("input") if isinstance(decision.get("input"), dict) else {},
                "metadata": {"autonomySource": "agent_runner", "triggerId": trigger_id},
            }
            output = await self._run_agent(agent_id, run_input)
            self._attempts[agent_id] = 0
            self._cooldowns[agent_id] = time.time()
            result = {"status": "ok", "agent": agent_id, "output": output, "triggerId": trigger_id}
            result["planStep"] = plan_step
            await self._publish_event(
                {
                    "type": "AGENT_ACTION_COMPLETE",
                    "message": f"Agent {agent_id} completed autonomy action",
                    "result": result,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "metadata": {"autonomySource": "agent_runner", "triggerId": trigger_id, "planStep": plan_step},
                }
            )
            return result
        except Exception as exc:
            result = {
                "status": "failed",
                "agent": agent_id,
                "error": str(exc),
                "attempt": self._attempts[agent_id],
                "triggerId": trigger_id,
                "planStep": plan_step,
            }
            await self._publish_event(
                {
                    "type": "AGENT_ACTION_FAILED",
                    "message": f"Agent {agent_id} failed autonomy action",
                    "reason": str(exc),
                    "result": result,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "metadata": {"autonomySource": "agent_runner", "triggerId": trigger_id, "planStep": plan_step},
                }
            )
            return result
