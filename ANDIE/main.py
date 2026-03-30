from core.autonomous_orchestrator import AutonomousOrchestrator
try:
    from valhalla.remote_nodes_config import REMOTE_VALHALLA_ENDPOINTS
except ImportError:
    REMOTE_VALHALLA_ENDPOINTS = []
from core.goal_dependency_graph import GoalDependencyGraph
import time

if __name__ == "__main__":
    andie = AutonomousOrchestrator(remote_endpoints=REMOTE_VALHALLA_ENDPOINTS)
    graph = GoalDependencyGraph()

    # Example: Multi-step goal with dependencies
    fetch = graph.add_goal("fetch data", priority=2)
    process = graph.add_goal("process data", priority=3, dependencies=[fetch])
    visualize = graph.add_goal("visualize data", priority=4, dependencies=[process])

    while True:
        ready_goals = graph.get_ready_goals()
        if not ready_goals:
            print("[ANDIE] No ready goals.")
            break
        # Pick highest priority ready goal
        ready_goals.sort(key=lambda n: n.priority)
        goal = ready_goals[0]
        print(f"\n=== GOAL: {goal.description} (priority {goal.priority}) ===")
        result = andie.run_goal(goal.description)
        print("RESULT:", result)
        graph.mark_completed(goal)
        time.sleep(1)
