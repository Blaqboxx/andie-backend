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

    def run_goal(self, goal: str):
        print(f"[ANDIE] Goal received: {goal}")
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
        # Log metrics after each goal run
        if hasattr(self, 'node_scheduler'):
            log_metrics(self.node_scheduler.metrics)

                if evaluation["success"]:
                    break

                print("[ANDIE] Retry triggered...")
                retries += 1

            if retries == MAX_RETRIES:
                print("[ANDIE] Step failed after retries")
                return result

        print("[ANDIE] Goal completed.")
        return {"status": "GOAL_COMPLETE"}
