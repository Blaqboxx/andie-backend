import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from autonomy.agent_runner import AgentRunner
from autonomy.decision_engine import DecisionLayer
from autonomy.rule_evaluator import match_rule
from autonomy.trigger_engine import TriggerEngine


class RuleEvaluatorTests(unittest.TestCase):
    def test_match_rule_with_nested_conditions(self):
        rule = {
            "id": "nested",
            "enabled": True,
            "when": {
                "eventType": "STREAM_ERROR",
                "conditions": [
                    {"field": "severity", "operator": "==", "value": "error"},
                    {"field": "metadata.source", "operator": "==", "value": "encoder"},
                ],
            },
        }
        event = {
            "type": "STREAM_ERROR",
            "severity": "error",
            "metadata": {"source": "encoder"},
        }
        self.assertTrue(match_rule(rule, event))


class TriggerEngineTests(unittest.TestCase):
    def setUp(self):
        self.published = []
        self.executions = []

        async def run_agent(agent_name, payload):
            self.executions.append({"agent": agent_name, "payload": payload})
            return {"ok": True, "agent": agent_name}

        async def publish_event(payload):
            self.published.append(payload)
            return payload

        self.runner = AgentRunner(run_agent=run_agent, publish_event=publish_event)
        self.decision = DecisionLayer(memory_limit=10)

    def _write_rules(self, rules):
        handle, path = tempfile.mkstemp(prefix="andie-rules-", suffix=".json")
        Path(path).write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return Path(path)

    def test_trigger_engine_runs_agent_once_during_cooldown(self):
        rules_path = self._write_rules(
            [
                {
                    "id": "rule_stream_error_recovery",
                    "enabled": True,
                    "priority": 10,
                    "when": {
                        "eventType": "STREAM_ERROR",
                        "conditions": [
                            {"field": "severity", "operator": "==", "value": "error"},
                        ],
                    },
                    "then": {
                        "action": "TRIGGER_AGENT",
                        "agent": "self_healing_agent",
                        "maxRetries": 3,
                        "agentCooldownMs": 1000,
                    },
                    "cooldownMs": 60_000,
                }
            ]
        )

        engine = TriggerEngine(
            rules_path=rules_path,
            decision_layer=self.decision,
            agent_runner=self.runner,
        )

        event = {
            "type": "STREAM_ERROR",
            "severity": "error",
            "updatedAt": "2026-01-01T00:00:00Z",
            "metadata": {"source": "encoder"},
        }

        first = asyncio.run(engine.process_event(event))
        second = asyncio.run(engine.process_event(event))

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(len(self.executions), 1)

    def test_ignores_events_emitted_by_autonomy_pipeline(self):
        rules_path = self._write_rules(
            [
                {
                    "id": "rule_task_failure_recovery",
                    "enabled": True,
                    "priority": 10,
                    "when": {
                        "eventType": "TASK_FAILURE",
                        "conditions": [
                            {"field": "severity", "operator": "==", "value": "error"},
                        ],
                    },
                    "then": {
                        "action": "TRIGGER_AGENT",
                        "agent": "self_healing_agent",
                    },
                    "cooldownMs": 0,
                }
            ]
        )

        engine = TriggerEngine(
            rules_path=rules_path,
            decision_layer=self.decision,
            agent_runner=self.runner,
        )

        event = {
            "type": "TASK_FAILURE",
            "severity": "error",
            "metadata": {"autonomySource": "agent_runner"},
        }

        matched = asyncio.run(engine.process_event(event))
        self.assertEqual(matched, [])
        self.assertEqual(len(self.executions), 0)


if __name__ == "__main__":
    unittest.main()
