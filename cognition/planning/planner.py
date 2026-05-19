"""
Planner — converts a natural-language goal into a validated TaskGraph.

The Planner is ANDIE's strategic cognition layer for STEP 6.  It:

  1. Accepts a goal string
  2. Attempts LLM-based decomposition (if LLM is reachable)
  3. Falls back to heuristic decomposition if LLM is unavailable
  4. Validates the resulting graph (no cycles, no dangling refs)
  5. Returns a ready-to-execute TaskGraph

The Planner intentionally does NOT execute the graph — that is the
responsibility of TaskGraph.execute().  Clean separation of planning
from execution is a core architectural principle.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, List, Optional

from .dependency_engine import DependencyEngine
from .task_graph import TaskGraph
from .task_models import TaskNode, TaskPriority, TaskStatus

# Optional LLM
try:
    from andie_backend.brain.llm_router import call_llm as _call_llm
    _LLM_AVAILABLE = True
except Exception:
    _LLM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Heuristic task templates
# ---------------------------------------------------------------------------

# Maps goal keywords → ordered list of (id, description, dependencies, priority)
_HEURISTIC_PLANS: List[tuple[str, List[Dict[str, Any]]]] = [
    (
        "deploy",
        [
            {"id": "validate_config",   "description": "Validate configuration files and environment variables",
             "dependencies": [],                        "priority": "critical"},
            {"id": "setup_environment", "description": "Set up execution environment (install dependencies)",
             "dependencies": ["validate_config"],       "priority": "high"},
            {"id": "run_tests",         "description": "Run test suite to validate code quality",
             "dependencies": ["setup_environment"],     "priority": "high"},
            {"id": "build_artifact",    "description": "Build deployable artifact",
             "dependencies": ["run_tests"],             "priority": "critical"},
            {"id": "deploy_service",    "description": "Deploy the service to target environment",
             "dependencies": ["build_artifact"],        "priority": "critical"},
            {"id": "validate_deploy",   "description": "Run smoke tests against deployed service",
             "dependencies": ["deploy_service"],        "priority": "high"},
        ],
    ),
    (
        "docker",
        [
            {"id": "check_docker",      "description": "Verify Docker daemon is running",
             "dependencies": [],                        "priority": "critical"},
            {"id": "build_image",       "description": "Build Docker image from Dockerfile",
             "dependencies": ["check_docker"],          "priority": "critical"},
            {"id": "push_image",        "description": "Push Docker image to registry",
             "dependencies": ["build_image"],           "priority": "high"},
            {"id": "deploy_container",  "description": "Deploy container from built image",
             "dependencies": ["push_image"],            "priority": "critical"},
            {"id": "health_check",      "description": "Verify container health endpoint responds",
             "dependencies": ["deploy_container"],      "priority": "high"},
        ],
    ),
    (
        "api",
        [
            {"id": "setup_deps",        "description": "Install API dependencies",
             "dependencies": [],                        "priority": "high"},
            {"id": "generate_api",      "description": "Generate API implementation",
             "dependencies": ["setup_deps"],            "priority": "critical"},
            {"id": "run_api_tests",     "description": "Run API test suite",
             "dependencies": ["generate_api"],          "priority": "high"},
            {"id": "validate_api",      "description": "Validate API endpoints and schemas",
             "dependencies": ["run_api_tests"],         "priority": "critical"},
        ],
    ),
    (
        "database",
        [
            {"id": "provision_db",      "description": "Provision database instance",
             "dependencies": [],                        "priority": "critical"},
            {"id": "run_migrations",    "description": "Apply database schema migrations",
             "dependencies": ["provision_db"],          "priority": "critical"},
            {"id": "seed_data",         "description": "Seed initial data if required",
             "dependencies": ["run_migrations"],        "priority": "medium"},
            {"id": "validate_db",       "description": "Validate database connectivity and schema",
             "dependencies": ["run_migrations"],        "priority": "high"},
        ],
    ),
    (
        "ai platform",
        [
            {"id": "setup_docker",      "description": "Set up Docker environment",
             "dependencies": [],                        "priority": "critical"},
            {"id": "create_network",    "description": "Create Docker network for AI services",
             "dependencies": ["setup_docker"],          "priority": "high"},
            {"id": "deploy_database",   "description": "Deploy vector/relational database",
             "dependencies": ["create_network"],        "priority": "critical"},
            {"id": "validate_db",       "description": "Validate database is responsive",
             "dependencies": ["deploy_database"],       "priority": "high"},
            {"id": "deploy_api",        "description": "Deploy AI API service",
             "dependencies": ["validate_db"],           "priority": "critical"},
            {"id": "validate_api",      "description": "Validate API health and endpoints",
             "dependencies": ["deploy_api"],            "priority": "high"},
            {"id": "deploy_frontend",   "description": "Deploy frontend interface",
             "dependencies": ["validate_api"],          "priority": "medium"},
            {"id": "validate_frontend", "description": "Validate frontend loads and connects",
             "dependencies": ["deploy_frontend"],       "priority": "medium"},
        ],
    ),
    (
        "build",
        [
            {"id": "install_deps",      "description": "Install project dependencies",
             "dependencies": [],                        "priority": "high"},
            {"id": "generate_code",     "description": "Generate implementation code",
             "dependencies": ["install_deps"],          "priority": "critical"},
            {"id": "run_tests",         "description": "Run test suite against generated code",
             "dependencies": ["generate_code"],         "priority": "high"},
            {"id": "validate_output",   "description": "Validate output artifacts",
             "dependencies": ["run_tests"],             "priority": "high"},
        ],
    ),
]

_GENERIC_PLAN: List[Dict[str, Any]] = [
    {"id": "analyze",    "description": "Analyze goal requirements",
     "dependencies": [],              "priority": "high"},
    {"id": "implement",  "description": "Implement the solution",
     "dependencies": ["analyze"],     "priority": "critical"},
    {"id": "validate",   "description": "Validate the implementation",
     "dependencies": ["implement"],   "priority": "high"},
]


# ---------------------------------------------------------------------------
# LLM plan parsing
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = """\
You are ANDIE, an autonomous planning engine.
Decompose the given goal into an ordered list of discrete, executable subtasks.

Return ONLY a JSON array. Each element must have these keys:
  id           (string, snake_case, unique)
  description  (string, concise action)
  dependencies (array of id strings — must reference ids that appear earlier)
  priority     (one of: critical, high, medium, low)

Rules:
- Keep subtasks focused and independently executable.
- Use dependency ids that you have already defined in the array.
- Do not include circular dependencies.
- Return 3–10 nodes for most goals.
- Return ONLY the JSON array, no explanatory text.
"""


def _parse_llm_plan(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract a JSON array of task dicts from LLM output."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    # Find first [ ... ]
    start = text.find("[")
    end   = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start: end + 1])
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            return parsed
    except Exception:
        pass
    return None


def _dicts_to_nodes(raw: List[Dict[str, Any]]) -> List[TaskNode]:
    """Convert raw plan dicts to TaskNode objects."""
    nodes: List[TaskNode] = []
    for item in raw:
        priority_val = item.get("priority", "medium")
        try:
            priority = TaskPriority(priority_val)
        except ValueError:
            priority = TaskPriority.MEDIUM

        nodes.append(TaskNode(
            id=str(item.get("id", f"task_{len(nodes)}")),
            description=str(item.get("description", "unnamed task")),
            dependencies=[str(d) for d in item.get("dependencies", [])],
            priority=priority,
        ))
    return nodes


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """
    Converts a natural-language goal into a validated, ready-to-execute TaskGraph.

    Strategy
    --------
    1. Try LLM decomposition (if LLM available)
    2. Fall back to heuristic keyword matching
    3. Fall back to generic 3-step plan
    4. Validate + return TaskGraph
    """

    def __init__(self) -> None:
        self._dep_engine = DependencyEngine()

    def plan(
        self,
        goal: str,
        *,
        force_heuristic: bool = False,
        event_cb: Optional[Any] = None,
    ) -> TaskGraph:
        """
        Decompose *goal* into a TaskGraph.

        Parameters
        ----------
        goal:
            Natural-language goal description.
        force_heuristic:
            Skip LLM and use heuristic decomposition directly.
        event_cb:
            Optional async or sync event callback for the TaskGraph.
        """
        nodes = self._decompose(goal, force_heuristic=force_heuristic)
        graph = TaskGraph(goal=goal, nodes=nodes, event_cb=event_cb)
        # Validate — will raise on cycles or dangling refs
        try:
            graph.validate()
        except Exception:
            # If LLM produced an invalid plan, fall back to heuristic
            nodes = self._heuristic_decompose(goal)
            graph = TaskGraph(goal=goal, nodes=nodes, event_cb=event_cb)
            graph.validate()  # heuristics are always valid

        return graph

    def _decompose(self, goal: str, *, force_heuristic: bool) -> List[TaskNode]:
        """Try LLM first, then heuristic."""
        if not force_heuristic and _LLM_AVAILABLE:
            try:
                raw_text = _call_llm(
                    f"Goal: {goal}\n\nDecompose this into subtasks.",
                    _PLAN_SYSTEM_PROMPT,
                )
                parsed = _parse_llm_plan(str(raw_text))
                if parsed:
                    return _dicts_to_nodes(parsed)
            except Exception:
                pass

        return self._heuristic_decompose(goal)

    def _heuristic_decompose(self, goal: str) -> List[TaskNode]:
        """Keyword-match against known plan templates."""
        goal_lower = goal.lower()
        # Find best matching template (longest keyword match wins)
        best_match: Optional[List[Dict[str, Any]]] = None
        best_score = 0
        for keyword, template in _HEURISTIC_PLANS:
            if keyword in goal_lower:
                score = len(keyword)
                if score > best_score:
                    best_score = score
                    best_match = template

        raw = best_match if best_match else _GENERIC_PLAN
        return _dicts_to_nodes(raw)

    def explain(self, graph: TaskGraph) -> str:
        """Return a human-readable execution plan for display / logging."""
        lines = [f"Goal: {graph.goal}", f"Graph: {graph.graph_id}", ""]
        groups = DependencyEngine().parallel_groups(graph.nodes)
        for i, group in enumerate(groups, 1):
            ids = ", ".join(f"{n.id} ({n.priority.value})" for n in group)
            lines.append(f"  Wave {i}: {ids}")
        return "\n".join(lines)
