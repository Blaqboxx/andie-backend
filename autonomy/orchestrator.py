
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Existing imports (adapt to your actual paths)

from andie_backend.autonomy.reasoning_engine import ReasoningEngine
from andie_backend.autonomy.trigger_engine import TriggerEngine
from andie_backend.autonomy.knowledge_integration import KnowledgeIntegrator
from andie_backend.andie.memory.memory_service import MemoryService
from andie_backend.andie.fusion.logic import FusionEngine
from andie_backend.andie.feedback.tracker import FeedbackTracker
from andie_backend.autonomy.plan_persistence import PlanPersistence

# =========================
# 🧬 Cognitive State
# =========================

@dataclass
class CognitiveState:
    # Input
    observations: List[Any] = field(default_factory=list)
    goals: List[Dict] = field(default_factory=list)

    # Memory
    retrieved_memories: List[Dict] = field(default_factory=list)
    working_memory: Dict = field(default_factory=dict)

    # Reasoning
    thoughts: List[str] = field(default_factory=list)
    plan: Dict = field(default_factory=dict)

    # Execution
    pending_tasks: List[Dict] = field(default_factory=list)
    completed_tasks: List[Dict] = field(default_factory=list)

    # Meta
    confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)

# =========================
# 🔁 Orchestrator
# =========================

class AndieOrchestrator:

    def _simulate_and_select(self, state, tasks):
        import random
        scored = []
        for task in tasks:
            pred = self._predict_outcome(state, task)
            score = pred["success_probability"]
            scored.append((score, task, pred))
        scored.sort(reverse=True, key=lambda x: x[0])
        # Fallback exploration: 20% chance
        if random.random() < 0.2:
            best_score, best_task, best_pred = random.choice(scored)
        else:
            best_score, best_task, best_pred = scored[0]
        best_task["prediction"] = best_pred
        best_task["chosen_from"] = len(tasks)
        return best_task

    def _predict_outcome(self, state, task):
        history = self.memory.retrieve({
            "type": "execution_trace",
            "action": task["action"],
            "goal": state.plan.get("goal")
        })
        if not history:
            return {"success_probability": 0.5, "risk": 0.5}
        success, total = 0, 0
        for h in history:
            for t in h.get("tasks", []):
                if t["action"] != task["action"]:
                    continue
                total += 1
                if t["status"] == "done":
                    success += 1
        p = success / (total or 1)
        return {"success_probability": p, "risk": 1 - p}

    def _log_event(self, state):
        log = {
            "goal": state.metadata.get("goal"),
            "plan_id": state.plan.get("id"),
            "tasks": state.completed_tasks,
            "confidence": state.confidence
        }
        # Use store_memory for MemoryService
        self.memory.store_memory({
            "type": "system_log",
            "data": log
        }, metadata={"tags": ["system_log"]}, user_input="system log event")

    def _init_meta_learning(self):
        from andie_backend.andie.brain.meta_learning import MetaLearningEngine
        self.meta_learning = MetaLearningEngine(self.memory)


    # --- Production System Layer ---
    def log_event(self, event):
        # Simple structured logging (replace with real logger as needed)
        print("[LOG]", event)

    def _init_task_queue(self):
        # In-memory queue (replace with Redis, etc. for prod)
        from queue import Queue
        self.task_queue = Queue()

    def enqueue_task(self, task):
        if not hasattr(self, "task_queue"):
            self._init_task_queue()
        self.task_queue.put(task)

    def dequeue_task(self):
        if not hasattr(self, "task_queue"):
            self._init_task_queue()
        if self.task_queue.empty():
            return None
        return self.task_queue.get()

    def _execute_with_timeout(self, func, *args, timeout=10, **kwargs):
        import threading
        result = {}
        def target():
            try:
                result["value"] = func(*args, **kwargs)
            except Exception as e:
                result["error"] = e
        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self.log_event({"type": "timeout", "func": func.__name__})
            return None, "timeout"
        if "error" in result:
            return None, result["error"]
        return result.get("value"), None

    def __init__(self, *args, **kwargs):
        # ...existing code...
        self.agent_stats = {}  # {agent_id: {action: {success_rate, count, avg_latency}}}
        super().__init__(*args, **kwargs)

    def _update_agent_stats(self, agent, task, success, latency):
        agent_id = getattr(agent, "role", str(agent))
        action = task.get("action")
        if agent_id not in self.agent_stats:
            self.agent_stats[agent_id] = {}
        if action not in self.agent_stats[agent_id]:
            self.agent_stats[agent_id][action] = {"success": 0, "count": 0, "latency": 0.0}
        stats = self.agent_stats[agent_id][action]
        stats["success"] += int(success)
        stats["count"] += 1
        stats["latency"] += latency
        stats["success_rate"] = stats["success"] / stats["count"]
        stats["avg_latency"] = stats["latency"] / stats["count"]

    def _select_agent(self, task):
        # Choose agent with highest success rate for this action
        candidates = [a for a in self.agents if hasattr(a, "role")]
        action = task.get("action")
        best = None
        best_score = -1
        for agent in candidates:
            agent_id = getattr(agent, "role", str(agent))
            stats = self.agent_stats.get(agent_id, {}).get(action, {})
            score = stats.get("success_rate", 0.5)
            if score > best_score:
                best = agent
                best_score = score
        return best or candidates[0]

    def predict_outcome(self, task, state):
        # Simple world model: predict success probability from past memory
        history = self.memory.retrieve({
            "type": "strategy_record",
            "action": task["action"],
            "goal": state.plan.get("goal")
        })
        if not history:
            return {"success_probability": 0.5, "risk": 0.5}
        successes = sum(1 for r in history if r.get("success"))
        total = len(history)
        success_rate = successes / total if total else 0.5
        return {"success_probability": success_rate, "risk": 1 - success_rate}

    def store_strategy_record(self, task, state):
        # Store a record of strategy execution for meta-learning
        self.memory.store_memory({
            "type": "strategy_record",
            "action": task.get("action"),
            "context": task.get("context"),
            "success": task.get("status") == "done",
            "confidence": state.confidence
        }, metadata={"tags": ["strategy_record"]}, user_input="strategy record")

    def update_strategy_scores(self):
        # Aggregate strategy records for meta-learning
        history = self.memory.retrieve({"type": "strategy_record"})
        # Aggregate by (action, context_hash)
        registry = {}
        for rec in history:
            key = (rec.get("action"), str(rec.get("context")))
            if key not in registry:
                registry[key] = {"success": 0, "count": 0, "confidence": 0.0}
            registry[key]["success"] += int(rec.get("success", False))
            registry[key]["count"] += 1
            registry[key]["confidence"] += rec.get("confidence", 0.0)
        # Compute aggregate stats
        for key, stats in registry.items():
            stats["success_rate"] = stats["success"] / stats["count"] if stats["count"] else 0.0
            stats["avg_confidence"] = stats["confidence"] / stats["count"] if stats["count"] else 0.0
        self.strategy_registry = registry

    def _ensure_goal_fields(self, goal):
        # Ensure all required fields exist for goal evaluation
        goal.setdefault("score", 0.0)
        goal.setdefault("progress", 0.0)
        goal.setdefault("success_history", [])
        goal.setdefault("priority", 0.0)
        goal.setdefault("plans", [])

    def _evaluate_goal(self, state):
        # Assume single-goal for now
        goal = state.metadata.get("goal")
        if not goal:
            if state.goals:
                goal = state.goals[0]
                state.metadata["goal"] = goal
            else:
                return None
        self._ensure_goal_fields(goal)
        tasks = state.plan.get("tasks", [])
        completed = sum(1 for t in tasks if t.get("status") == "done")
        total = len(tasks) or 1
        progress = completed / total
        goal["progress"] = progress
        goal["success_history"].append(progress)
        # Smooth score over last 5 cycles
        goal["score"] = sum(goal["success_history"][-5:]) / min(len(goal["success_history"]), 5)
        # Priority: combine score and progress
        goal["priority"] = goal["score"] * 0.5 + (1 - goal["progress"]) * 0.5
        return goal

    def _is_goal_stagnating(self, goal):
        history = goal.get("success_history", [])
        if len(history) < 5:
            return False
        # No improvement over last 5 cycles
        return max(history[-5:]) - min(history[-5:]) < 0.05

    def reflect(self, state):
        goal = self._evaluate_goal(state)
        if goal and self._is_goal_stagnating(goal):
            state.metadata["strategy_shift"] = True
        else:
            state.metadata.pop("strategy_shift", None)
        return state
    def _init_agents(self):
        from andie_backend.andie.core.agents.planner import PlannerAgent
        from andie_backend.andie.core.agents.executor import ExecutorAgent
        from andie_backend.andie.core.agents.analyst import AnalystAgent
        self.agents = [
            PlannerAgent(),
            ExecutorAgent(self.trigger),
            AnalystAgent()
        ]

    def run_agents(self, state):
        # Ensure agent_stats and meta_learning are always initialized
        if not hasattr(self, "agent_stats"):
            self.agent_stats = {}
        if not hasattr(self, "meta_learning"):
            self._init_meta_learning()
        import time
        for agent in self.agents:
            if hasattr(agent, "role") and agent.role == "executor" and state.plan.get("tasks"):
                # --- Simulation-based selection ---
                task = self._simulate_and_select(state, state.plan["tasks"])
                start = time.time()
                try:
                    result = self.trigger.execute(task)
                    latency = time.time() - start
                    task["result"] = result
                    task["status"] = "done"
                    self._update_agent_stats(agent, task, True, latency)
                except Exception as e:
                    self.log_event({"type": "agent_exception", "agent": getattr(agent, "role", str(agent)), "error": str(e)})
                    task["status"] = "failed"
                self.store_strategy_record(task, state)
            else:
                # Non-executor agents run as usual
                start = time.time()
                try:
                    result_state, error = self._execute_with_timeout(agent.run, state, timeout=10)
                    latency = time.time() - start
                    if error:
                        self.log_event({"type": "agent_error", "agent": getattr(agent, "role", str(agent)), "error": str(error)})
                        continue
                    self._update_agent_stats(agent, {"action": "_meta"}, True, latency)
                    state = result_state
                except Exception as e:
                    self.log_event({"type": "agent_exception", "agent": getattr(agent, "role", str(agent)), "error": str(e)})
                    continue
        # Periodically update strategy scores (could be every N cycles)
        self.update_strategy_scores()
        # Meta-learning aggregation (every cycle for now)
        self.meta_learning.aggregate()
        # Observability: log cycle
        self._log_event(state)
        return state

    def _build_context_signature(self, state, task):
        return {
            "action": task.get("action"),
            "goal": state.plan.get("goal"),
            "observation": str(state.observations)[:200],
        }

    def _should_replan(self, state):
        tasks = state.plan.get("tasks", [])
        failed = [t for t in tasks if t["status"] == "failed"]
        total = len(tasks) if tasks else 1
        failure_rate = len(failed) / total
        # Smart threshold
        if failure_rate > 0.4:
            return True
        # Repeated failure on same task
        for t in failed:
            if t["retries"] >= 3:
                return True
        return False

    def _replan(self, state):
        failed_tasks = [t for t in state.plan.get("tasks", []) if t["status"] == "failed"]
        failure_memory = self.memory.retrieve({
            "type": "execution_trace",
            "status": "failed",
            "action": [t["action"] for t in failed_tasks]
        })
        context = {
            "goal": state.plan.get("goal"),
            "failed_tasks": failed_tasks,
            "failure_patterns": failure_memory
        }
        return self.reasoning.replan(context)

    def _score_strategies(self, state, task):
        history = self.memory.retrieve({
            "type": "execution_trace",
            "action": task["action"],
            "goal": state.plan.get("goal")
        })
        if not history:
            return 0.5
        success = 0
        total = 0
        for h in history:
            for t in h.get("tasks", []):
                if t["action"] != task["action"]:
                    continue
                # Context match: exact goal for now
                if t.get("context", {}).get("goal") != state.plan.get("goal"):
                    continue
                total += 1
                if t["status"] == "done":
                    success += 1
        return success / (total or 1)

    def __init__(self):
        # Core systems
        self.memory = MemoryService()
        self.reasoning = ReasoningEngine()
        # Provide dummy arguments for TriggerEngine stub
        self.trigger = TriggerEngine(rules_path=None, decision_layer=None, agent_runner=None)
        self.knowledge = KnowledgeIntegrator()
        self.fusion = FusionEngine()
        self.feedback = FeedbackTracker()
        self.plan_persistence = PlanPersistence()
        self._init_agents()

    # =========================
    # 🔁 MAIN LOOP
    # =========================

    def run_cycle(self, input_data: Any, goals: List[Dict] = None) -> CognitiveState:
        # Try to load persisted plan
        loaded_plan = self.plan_persistence.load()
        state = CognitiveState(
            observations=[input_data],
            goals=goals or []
        )
        if loaded_plan:
            state.plan = loaded_plan
            state.pending_tasks = loaded_plan.get("tasks", [])

        state = self.observe(state)
        state = self.enrich(state)
        state = self.run_agents(state)
        state = self.reflect(state)

        return state

    # =========================
    # 👁️ OBSERVE
    # =========================

    def observe(self, state: CognitiveState) -> CognitiveState:
        # Normalize / preprocess input if needed
        state.metadata["observed_at"] = self._timestamp()
        return state

    # =========================
    # 🧠 ENRICH (Memory + Knowledge)
    # =========================

    def enrich(self, state: CognitiveState) -> CognitiveState:
        # Retrieve relevant memory
        memories = self.memory.retrieve(state.observations)
        state.retrieved_memories = memories

        # Inject knowledge
        knowledge = self.knowledge.enrich(state.observations)
        state.working_memory["knowledge"] = knowledge

        return state

    # =========================
    # 🤔 DECIDE (Reasoning + Planning)
    # =========================

    def decide(self, state: CognitiveState) -> CognitiveState:
        # If plan is active, do not overwrite
        if state.plan and state.plan.get("status") == "active":
            return state

        from andie_backend.autonomy.task_utils import initialize_task

        # Combine context
        context = {
            "observations": state.observations,
            "memory": state.retrieved_memories,
            "knowledge": state.working_memory.get("knowledge"),
            "goals": state.goals
        }

        # Reasoning step
        reasoning_output = self.reasoning.process(context)
        state.thoughts.append(reasoning_output.get("thought"))

        # Plan generation
        plan = reasoning_output.get("plan", {})
        # Normalize tasks
        plan["tasks"] = [initialize_task(t) for t in plan.get("tasks", [])]
        # Strategy scoring
        for task in plan["tasks"]:
            score = self._score_strategies(state, task)
            task["strategy_score"] = score
            if score < 0.3:
                task["priority"] = "low"
            elif score > 0.7:
                task["priority"] = "high"
        state.plan = plan
        state.pending_tasks = plan.get("tasks", [])

        return state

    # =========================
    # ⚙️ ACT (Execution Layer)
    # =========================

    def act(self, state: CognitiveState) -> CognitiveState:
        if not state.plan:
            return state

        tasks = state.plan.get("tasks", [])
        results = []

        for task in tasks:
            # Skip completed
            if task["status"] == "done":
                continue

            # Retry limit
            if task["retries"] >= 3:
                task["status"] = "failed"
                continue

            try:
                task["status"] = "running"

                result = self.trigger.execute({
                    "action": task["action"],
                    "params": task.get("params", {})
                })

                task["result"] = result
                task["status"] = "done"

            except Exception as e:
                task["error"] = str(e)
                task["status"] = "failed"
                task["retries"] += 1

            finally:
                task["last_updated"] = self._timestamp()
                results.append(task)

        state.completed_tasks = results

        return state

    # =========================
    # 🔁 REFLECT (Learning Layer)
    # =========================

    def reflect(self, state: CognitiveState) -> CognitiveState:
        tasks = state.plan.get("tasks", []) if state.plan else []

        completed = sum(1 for t in tasks if t["status"] == "done")
        failed = sum(1 for t in tasks if t["status"] == "failed")
        total = len(tasks) if tasks else 1

        state.plan["progress"] = completed / total

        # Store execution trace as first-class data
        episode = {
            "type": "execution_trace",
            "plan_id": state.plan.get("id"),
            "goal": state.plan.get("goal"),
            "tasks": [
                {
                    **t,
                    "context": self._build_context_signature(state, t)
                }
                for t in state.plan.get("tasks", [])
            ],
            "completed_tasks": state.completed_tasks,
            "timestamp": self._timestamp(),
            "confidence": state.confidence
        }
        self.memory.store_memory(episode)

        # Failure-aware replanning: signal-driven
        if self._should_replan(state):
            new_plan = self._replan(state)
            state.plan = new_plan
            self.plan_persistence.clear()
        elif completed == total:
            state.plan["status"] = "completed"
            self.plan_persistence.clear()
        else:
            state.plan["status"] = "active"
            self.plan_persistence.save(state.plan)

        # Feedback scoring
        feedback_score = self.feedback.evaluate(state.completed_tasks)
        state.confidence = feedback_score

        return state

    # =========================
    # 🧰 UTILS
    # =========================

    def _timestamp(self):
        import time
        return time.time()
