
import time
from core.global_sentinel import analyze_nodes, generate_repair_goals
from core.metrics_logger import log_metrics
from core.planner import Planner
from core.decision_engine import generate_code_from_goal
from core.evaluator import evaluate_result
from agents.executor_agent import LocalExecutorAgent, RemoteExecutorAgent
from core.node_scheduler import NodeScheduler
from core.memory_manager import store_execution


class AutonomousOrchestrator:
    def __init__(self, remote_endpoints=None):
        self.planner = Planner()
        self.local_executor = LocalExecutorAgent()
        self.remote_endpoints = remote_endpoints or []
        self.node_scheduler = NodeScheduler(self.remote_endpoints)

    def get_executor(self):
        # Use smart scheduler for remote nodes, fallback to local if none healthy
        if self.remote_endpoints:
            node_url = self.node_scheduler.select_node()
            if node_url:
                return RemoteExecutorAgent(node_url), node_url
        return self.local_executor, None

    async def submit(self, task):
        from core.memory_manager import store_event
        from agents.critic_agent import Critic
        # Sentinel, self-healing, and memory hooks can be added here
        if task["type"] == "conversation":
            message = task["payload"]["message"]
            stream = task.get("stream")
            store_event({"type": "chat", "role": "user", "content": message})
            # Multi-agent chat loop: Planner → Executor → Critic
            planner = self.planner
            critic = Critic()
            plan = planner.create_plan(message)
            for step in plan:
                step_desc = step["description"]
                # Stream planner's plan
                await stream(f"Planner: {step_desc}\n")
                # Generate code and execute
                code = generate_code_from_goal(step_desc)
                executor, node_url = self.get_executor()
                result = executor.run_task(code)
                await stream(f"Executor: {result}\n")
                # Critic reviews
                review = critic.review(step_desc, result)
                await stream(f"Critic: {review}\n")
                store_event({"type": "chat", "role": "planner", "content": step_desc})
                store_event({"type": "chat", "role": "executor", "content": str(result)})
                store_event({"type": "chat", "role": "critic", "content": review})
            return {"output": "Multi-agent chat complete."}
        # Fallback: treat as goal
        return self.run_goal(task.get("payload", {}).get("message", ""))

    def run_goal(self, goal: str):
        print(f"[ANDIE] Goal received: {goal}")
        # Self-healing: check for repair goals before user goal
        repair_goals = generate_repair_goals()
        for repair_goal in repair_goals:
            print(f"[SELF-HEALING] Executing repair goal: {repair_goal}")
            plan = self.planner.create_plan(repair_goal)
            for step in plan:
                code = generate_code_from_goal(step["description"])
                executor, node_url = self.get_executor()
                start = time.time()
                result = executor.run_task(code)
                latency = time.time() - start
                if node_url:
                    self.node_scheduler.update_metrics(node_url, result, latency)
                store_execution(step["description"], result)
                evaluate_result(result)
        # Global Sentinel execution guard
        sentinel_data = analyze_nodes()
        if sentinel_data and all(n["status"] == "blocked" for n in sentinel_data.values()):
            print("[SENTINEL] All nodes blocked. Halting execution.")
            return {"status": "HALTED", "reason": "All nodes unsafe"}
        plan = self.planner.create_plan(goal)
        MAX_RETRIES = 3
        for step in plan:
            retries = 0
            while retries < MAX_RETRIES:
                print(f"[ANDIE] Step {step['step']} Attempt {retries+1}")
                code = generate_code_from_goal(step["description"])
                executor, node_url = self.get_executor()
                start = time.time()
                result = executor.run_task(code)
                latency = time.time() - start
                # Record node health metrics
                if node_url:
                    self.node_scheduler.update_metrics(node_url, result, latency)
                store_execution(step["description"], result)
                evaluation = evaluate_result(result)
                if evaluation["success"]:
                    break
                print("[ANDIE] Retry triggered...")
                retries += 1
            if retries == MAX_RETRIES:
                print("[ANDIE] Step failed after retries")
                return result
        # Log metrics after each goal run
        if hasattr(self, 'node_scheduler'):
            log_metrics(self.node_scheduler.metrics)
        print("[ANDIE] Goal completed.")
        return {"status": "GOAL_COMPLETE"}



class AutonomousOrchestrator:
    def __init__(self, remote_endpoints=None):
        self.planner = Planner()
        self.local_executor = LocalExecutorAgent()
        self.remote_endpoints = remote_endpoints or []
        self.node_scheduler = NodeScheduler(self.remote_endpoints)

    def get_executor(self):
        # Use smart scheduler for remote nodes, fallback to local if none healthy
        if self.remote_endpoints:
            node_url = self.node_scheduler.select_node()
            if node_url:
                return RemoteExecutorAgent(node_url), node_url
        return self.local_executor, None

    def run_goal(self, goal: str):
        print(f"[ANDIE] Goal received: {goal}")
        # Self-healing: check for repair goals before user goal
        repair_goals = generate_repair_goals()
        for repair_goal in repair_goals:
            print(f"[SELF-HEALING] Executing repair goal: {repair_goal}")
            plan = self.planner.create_plan(repair_goal)
            for step in plan:
                code = generate_code_from_goal(step["description"])
                executor, node_url = self.get_executor()
                start = time.time()
                result = executor.run_task(code)
                latency = time.time() - start
                if node_url:
                    self.node_scheduler.update_metrics(node_url, result, latency)
                store_execution(step["description"], result)
                evaluate_result(result)
        # Global Sentinel execution guard
        sentinel_data = analyze_nodes()
        if sentinel_data and all(n["status"] == "blocked" for n in sentinel_data.values()):
            print("[SENTINEL] All nodes blocked. Halting execution.")
            return {"status": "HALTED", "reason": "All nodes unsafe"}
        plan = self.planner.create_plan(goal)
        MAX_RETRIES = 3
        for step in plan:
            retries = 0
            while retries < MAX_RETRIES:
                print(f"[ANDIE] Step {step['step']} Attempt {retries+1}")
                code = generate_code_from_goal(step["description"])
                executor, node_url = self.get_executor()
                start = time.time()
                result = executor.run_task(code)
                latency = time.time() - start
                # Record node health metrics
                if node_url:
                    self.node_scheduler.update_metrics(node_url, result, latency)
                store_execution(step["description"], result)
                evaluation = evaluate_result(result)
                if evaluation["success"]:
                    break
                print("[ANDIE] Retry triggered...")
                retries += 1
            if retries == MAX_RETRIES:
                print("[ANDIE] Step failed after retries")
                return result
        # Log metrics after each goal run
        if hasattr(self, 'node_scheduler'):
            log_metrics(self.node_scheduler.metrics)
        print("[ANDIE] Goal completed.")
        return {"status": "GOAL_COMPLETE"}
