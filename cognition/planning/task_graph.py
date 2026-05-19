"""
Task Graph — stateful execution container for ANDIE's goal decomposition.

TaskGraph holds all TaskNodes for a single goal, manages state transitions,
integrates with the epistemic engine per node, triggers adaptive retry via
RetryEngine, and records outcomes to ReflectionEngine.

STEP 7: Parallel Orchestration
  - Nodes are grouped into dependency waves via DependencyEngine.parallel_groups()
  - Nodes within the same wave have no ordering constraint → execute concurrently
  - asyncio.Semaphore limits max parallel tasks to prevent resource exhaustion
  - Each node is fully isolated: its own timeout, epistemic gate, retry loop,
    and reflection record.  A failure in one node never corrupts siblings.
  - Failures propagate after each wave completes, blocking dependent waves.

STEP 8: Resource-Aware Scheduling
  - Optional ResourceAwareScheduler injected at construction time
  - Before every wave: calls calculate_capacity() to get real-time adjusted
    max_parallel based on CPU / RAM / GPU / disk pressure
  - Emits ``resource_pressure`` events to the event bus when thresholds are breached
  - Semaphore is rebuilt per-wave using the adjusted capacity so concurrency
    dynamically tracks infrastructure load

This is the runtime brain of STEP 6+7+8.  It connects:

  DependencyEngine         →  wave grouping + failure propagation
  EpistemicEngine          →  per-node evidence evaluation
  RetryEngine              →  adaptive recovery per node
  ReflectionEngine         →  episodic memory per node outcome
  ResourceAwareScheduler   →  infrastructure-driven concurrency control
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from .dependency_engine import DependencyEngine
from .task_models import PlanResult, TaskNode, TaskPriority, TaskStatus

PY_BIN = sys.executable or "python3"

# Optional heavy imports — degrade gracefully if modules unavailable
try:
    from andie_backend.cognition.epistemic.engine import EpistemicEngine as _EpistemicEngine
    _EPISTEMIC_AVAILABLE = True
except Exception:
    _EPISTEMIC_AVAILABLE = False

try:
    from andie_backend.cognition.recovery.retry_engine import RetryEngine as _RetryEngine
    from andie_backend.cognition.recovery.recovery_models import RecoveryStrategy as _RecoveryStrategy
    _RETRY_ENGINE = _RetryEngine()
    _RECOVERY_AVAILABLE = True
except Exception:
    _RECOVERY_AVAILABLE = False

try:
    from andie_backend.cognition.reflection.reflection_engine import ReflectionEngine as _ReflectionEngine
    _GRAPH_REFLECTION = _ReflectionEngine(agent_id="andie-planner")
    _REFLECTION_AVAILABLE = True
except Exception:
    _REFLECTION_AVAILABLE = False

try:
    from andie_backend.cognition.resources.scheduler import ResourceAwareScheduler as _ResourceAwareScheduler
    _SCHEDULER_AVAILABLE = True
except Exception:
    _SCHEDULER_AVAILABLE = False


EventCallback = Callable[[Dict[str, Any]], Any]


class TaskGraph:
    """
    Stateful execution graph for a single goal.

    Usage
    -----
    graph = TaskGraph(goal="deploy AI platform", nodes=[...])
    graph.validate()
    result = await graph.execute(executor=my_executor_fn)
    """

    def __init__(
        self,
        goal: str,
        nodes: List[TaskNode],
        *,
        graph_id: Optional[str] = None,
        event_cb: Optional[EventCallback] = None,
        max_parallel: int = 4,
        scheduler: Optional[Any] = None,
    ) -> None:
        self.goal        = goal
        self.graph_id    = graph_id or f"graph-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        self._nodes: Dict[str, TaskNode] = {n.id: n for n in nodes}
        self._dep_engine = DependencyEngine()
        self._event_cb   = event_cb
        self._max_parallel = max_parallel
        # Optional ResourceAwareScheduler — when provided, parallelism is adjusted
        # dynamically before each wave based on real infrastructure metrics.
        self._scheduler: Optional[Any] = scheduler
        # asyncio.Semaphore is created lazily inside execute() (and rebuilt
        # per-wave when the scheduler adjusts capacity).
        self._semaphore: Optional[asyncio.Semaphore] = None
        # Per-graph prior strategy history: node_id → list of strategy strings
        self._prior_strategies: Dict[str, List[str]] = {n.id: [] for n in nodes}
        self._prior_reasons:    Dict[str, List[str]] = {n.id: [] for n in nodes}

    # ------------------------------------------------------------------ #
    # Properties                                                           #
    # ------------------------------------------------------------------ #

    @property
    def nodes(self) -> List[TaskNode]:
        return list(self._nodes.values())

    @property
    def node_map(self) -> Dict[str, TaskNode]:
        return dict(self._nodes)

    # ------------------------------------------------------------------ #
    # Validation                                                           #
    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        """Validate graph structure (cycles, dangling refs). Raises on error."""
        self._dep_engine.validate(self.nodes)

    # ------------------------------------------------------------------ #
    # Execution                                                            #
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        executor: Callable[[TaskNode], Any],
        *,
        max_node_retries: int = 3,
        node_timeout: int = 120,
        max_parallel: Optional[int] = None,
    ) -> PlanResult:
        """
        Execute all nodes in parallel waves.

        Nodes whose dependencies are all satisfied form a "wave".  Every node
        in the same wave is dispatched concurrently via asyncio.gather.
        A semaphore caps the number of concurrently running nodes to prevent
        resource exhaustion.

        After each wave, any failed nodes propagate BLOCKED status to their
        dependents before the next wave is evaluated.

        Parameters
        ----------
        executor:
            Async or sync callable ``executor(node) -> dict`` with keys:
            ``exit_code``, ``stdout``, ``stderr``, ``status``.
        max_node_retries:
            Global cap on per-node retries.
        node_timeout:
            Seconds per node execution attempt before TimeoutError.
        max_parallel:
            Override the graph-level concurrency cap.  Defaults to the
            value set in __init__ (4).

        Returns
        -------
        PlanResult — full outcome across all nodes.
        """
        base_concurrency = max_parallel or self._max_parallel
        # Initial semaphore — may be rebuilt per-wave when scheduler is active
        self._semaphore = asyncio.Semaphore(base_concurrency)

        await self._emit({
            "event": "graph_start",
            "graph_id": self.graph_id,
            "goal": self.goal,
            "total_nodes": len(self._nodes),
            "max_parallel": base_concurrency,
            "scheduler_active": self._scheduler is not None,
        })

        # Set max retries on all nodes
        for node in self._nodes.values():
            node.max_retries = max(node.max_retries, max_node_retries)

        # Compute execution waves once (static DAG)
        waves = self._dep_engine.parallel_groups(self.nodes)

        await self._emit({
            "event": "plan_waves",
            "graph_id": self.graph_id,
            "total_waves": len(waves),
            "waves": [[n.id for n in w] for w in waves],
        })

        for wave_idx, wave in enumerate(waves):
            # Filter out already-terminal nodes (blocked/failed propagated earlier)
            pending = [
                n for n in wave
                if n.status in (TaskStatus.PENDING, TaskStatus.READY)
            ]

            # Skip nodes whose dependency chain already failed
            runnable: List[TaskNode] = []
            for node in pending:
                blocked_by = self._check_blocked(node)
                if blocked_by:
                    node.mark_blocked(because_of=blocked_by)
                    await self._emit({
                        "event": "node_blocked",
                        "graph_id": self.graph_id,
                        "node_id": node.id,
                        "wave": wave_idx + 1,
                        "because_of": blocked_by,
                    })
                else:
                    runnable.append(node)

            if not runnable:
                continue

            # ── STEP 8: Resource-aware capacity check ─────────────────
            # Ask the scheduler for the current safe concurrency level.
            # If no scheduler is provided, fall back to the static cap.
            wave_concurrency = base_concurrency
            if self._scheduler is not None:
                try:
                    wave_concurrency = await self._scheduler.calculate_capacity(
                        requested=base_concurrency,
                        active_tasks=len(runnable),
                    )
                    decision = self._scheduler.latest_decision()
                    if decision is not None and decision.pressure_level.value != "none":
                        self._scheduler.emit_pressure_event(self._emit_sync, decision)
                except Exception:
                    pass  # scheduler errors must never block execution

            # Rebuild semaphore for this wave with the adjusted concurrency
            self._semaphore = asyncio.Semaphore(wave_concurrency)

            await self._emit({
                "event": "wave_start",
                "graph_id": self.graph_id,
                "wave": wave_idx + 1,
                "node_ids": [n.id for n in runnable],
                "concurrent": len(runnable),
                "wave_concurrency": wave_concurrency,
            })

            # ── Concurrent wave execution ─────────────────────────────
            await asyncio.gather(
                *[
                    self._execute_node_isolated(node, executor, node_timeout)
                    for node in runnable
                ],
                return_exceptions=False,   # exceptions are caught inside each task
            )

            await self._emit({
                "event": "wave_complete",
                "graph_id": self.graph_id,
                "wave": wave_idx + 1,
                "results": {
                    n.id: n.status.value for n in runnable
                },
            })

            # Propagate failures from this wave before the next wave evaluates
            newly_blocked = self._dep_engine.propagate_failures(self.nodes)
            if newly_blocked:
                await self._emit({
                    "event": "failure_propagated",
                    "graph_id": self.graph_id,
                    "blocked_nodes": [n.id for n in newly_blocked],
                })

        result = self._build_result()
        await self._emit({
            "event": "graph_complete",
            "graph_id": self.graph_id,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "blocked": result.blocked,
            "overall_confidence": round(result.overall_confidence, 3),
            "fully_successful": result.fully_successful,
        })
        return result

    async def _execute_node_isolated(
        self,
        node: TaskNode,
        executor: Callable[[TaskNode], Any],
        timeout: int,
    ) -> None:
        """
        Semaphore-guarded wrapper around _execute_node.

        The semaphore limits concurrency across the whole graph.
        Any exception is caught and translated to a FAILED node state so
        sibling tasks in the same wave are never affected.
        """
        assert self._semaphore is not None  # set in execute()
        async with self._semaphore:
            try:
                await self._execute_node(node, executor, timeout)
            except Exception as exc:
                # Isolation: unexpected errors mark node failed, do not propagate
                node.mark_failed(
                    reason=f"Unhandled exception in executor: {exc}",
                    exit_code=-1,
                )
                await self._emit({
                    "event": "node_exception",
                    "graph_id": self.graph_id,
                    "node_id": node.id,
                    "error": str(exc),
                })

    async def _execute_node(
        self,
        node: TaskNode,
        executor: Callable[[TaskNode], Any],
        timeout: int,
    ) -> None:
        """Run a single node with adaptive retry orchestration."""
        attempt = 0

        while True:
            attempt += 1
            node.status = TaskStatus.RUNNING
            node.mark_running()

            await self._emit({
                "event": "node_start",
                "graph_id": self.graph_id,
                "node_id": node.id,
                "description": node.description,
                "attempt": attempt,
            })

            # ── Execute ──────────────────────────────────────────────
            raw_result: Dict[str, Any] = {}
            try:
                raw_result = await asyncio.wait_for(
                    self._run_executor(executor, node),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raw_result = {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"TimeoutExpired: node '{node.id}' exceeded {timeout}s",
                    "status": "error",
                }
            except Exception as exc:
                raw_result = {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": str(exc),
                    "status": "error",
                }

            exit_code = int(raw_result.get("exit_code", -1))
            stdout    = str(raw_result.get("stdout") or "")
            stderr    = str(raw_result.get("stderr") or "")
            raw_status = "success" if exit_code == 0 else "error"

            # ── Epistemic gate ───────────────────────────────────────
            epistemic: Dict[str, Any] = {
                "status": raw_status, "confidence": 1.0 if exit_code == 0 else 0.0,
                "validated": exit_code == 0, "contradictions": [], "warnings": [],
                "raw_status": raw_status,
            }
            if _EPISTEMIC_AVAILABLE:
                try:
                    _eng = _EpistemicEngine(f"planner-{node.id}")
                    epistemic = _eng.evaluate({
                        "status": raw_status,
                        "exit_code": exit_code,
                        "stdout": stdout,
                        "stderr": stderr,
                        "iterations": attempt,
                    })
                except Exception:
                    pass

            ep_status   = epistemic.get("status", raw_status)
            confidence  = float(epistemic.get("confidence", 0.0))
            node.epistemic_status = ep_status
            node.confidence = confidence

            # ── Success path ─────────────────────────────────────────
            if ep_status in ("success", "success_with_warnings"):
                node.mark_success(confidence=confidence, stdout=stdout, stderr=stderr)
                node.exit_code = exit_code

                await self._emit({
                    "event": "node_success",
                    "graph_id": self.graph_id,
                    "node_id": node.id,
                    "confidence": confidence,
                    "attempt": attempt,
                    "epistemic_status": ep_status,
                })

                self._reflect_node(node, epistemic)
                return  # done

            # ── Failure path ─────────────────────────────────────────
            node.retry_count = attempt - 1  # before this attempt

            # Adaptive recovery
            recovery_patch: Dict[str, Any] = {}
            chosen_strategy = "none"
            if _RECOVERY_AVAILABLE:
                try:
                    _rec_strategy: Optional[str] = None
                    if _REFLECTION_AVAILABLE:
                        try:
                            _rec = _GRAPH_REFLECTION.recommend_recovery(node.description)
                            _rec_strategy = _rec.value if _rec else None
                        except Exception:
                            pass

                    _ctx = _RETRY_ENGINE.build_retry_context(
                        task=node.description,
                        job_id=f"{self.graph_id}:{node.id}",
                        failure_reason=node.failure_reason or "",
                        exit_code=exit_code,
                        stderr=stderr,
                        stdout=stdout,
                        confidence=confidence,
                        contradictions=epistemic.get("contradictions", []),
                        warnings=epistemic.get("warnings", []),
                        attempt_number=attempt,
                        prior_strategies=self._prior_strategies[node.id],
                        prior_failure_reasons=self._prior_reasons[node.id],
                        recommended_strategy=_rec_strategy,
                    )
                    _strategy = _RETRY_ENGINE.select_strategy(_ctx)
                    recovery_patch = _RETRY_ENGINE.execute(
                        _ctx, _strategy,
                        current_run_command=node.run_command or "",
                    )
                    chosen_strategy = _strategy.value
                    self._prior_strategies[node.id].append(f"{chosen_strategy}:attempt-{attempt}")
                    node.recovery_strategy = chosen_strategy

                    # Apply run_command patch immediately to node
                    if not recovery_patch.get("skip") and recovery_patch.get("run_command"):
                        node.run_command = recovery_patch["run_command"]
                except Exception:
                    pass

            # Record failure reason for next iteration
            failure_reason = (
                f"exit_code={exit_code}; ep={ep_status}; "
                f"stderr={stderr[:120]}"
            )
            node.failure_reason = failure_reason
            self._prior_reasons[node.id].append(failure_reason)

            await self._emit({
                "event": "node_failed",
                "graph_id": self.graph_id,
                "node_id": node.id,
                "attempt": attempt,
                "exit_code": exit_code,
                "confidence": confidence,
                "epistemic_status": ep_status,
                "recovery_strategy": chosen_strategy,
                "recovery_notes": recovery_patch.get("notes", ""),
                "will_retry": attempt < node.max_retries and not recovery_patch.get("skip"),
            })

            # Retry decision
            can_retry = (
                attempt < node.max_retries
                and not recovery_patch.get("skip")
                and chosen_strategy != "manual_intervention"
            )
            if not can_retry:
                node.mark_failed(
                    reason=failure_reason,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    confidence=confidence,
                )
                self._reflect_node(node, epistemic)
                return

            # Continue retry loop
            node.status = TaskStatus.RETRYING
            node.retry_count = attempt

    async def _run_executor(
        self, executor: Callable[[TaskNode], Any], node: TaskNode
    ) -> Dict[str, Any]:
        result = executor(node)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _check_blocked(self, node: TaskNode) -> Optional[str]:
        """Return the ID of the first failed dependency, or None."""
        for dep_id in node.dependencies:
            dep = self._nodes.get(dep_id)
            if dep and dep.status == TaskStatus.FAILED:
                return dep_id
            if dep and dep.status == TaskStatus.BLOCKED:
                return dep_id
        return None

    def _reflect_node(self, node: TaskNode, epistemic: Dict[str, Any]) -> None:
        """Persist node outcome to reflection memory."""
        if not _REFLECTION_AVAILABLE:
            return
        try:
            _GRAPH_REFLECTION.reflect(
                build_result={
                    "task":       node.description,
                    "job_id":     f"{self.graph_id}:{node.id}",
                    "exit_code":  node.exit_code or -1,
                    "stdout":     node.stdout,
                    "stderr":     node.stderr,
                    "iterations": node.retry_count + 1,
                },
                epistemic_state=epistemic,
            )
        except Exception:
            pass

    def _build_result(self) -> PlanResult:
        """Aggregate all node statuses into a PlanResult."""
        nodes = self.nodes
        stats = self._dep_engine.stats(nodes)
        confidences = [n.confidence for n in nodes if n.status == TaskStatus.SUCCESS]
        overall_conf = sum(confidences) / len(confidences) if confidences else 0.0
        critical_failures = [
            n.id for n in nodes
            if n.priority == TaskPriority.CRITICAL and n.status == TaskStatus.FAILED
        ]
        return PlanResult(
            goal=self.goal,
            total_nodes=len(nodes),
            succeeded=stats.get("success", 0),
            failed=stats.get("failed", 0),
            blocked=stats.get("blocked", 0),
            skipped=stats.get("skipped", 0),
            overall_confidence=overall_conf,
            node_summaries=[n.to_summary() for n in nodes],
            critical_failures=critical_failures,
        )

    async def _emit(self, payload: Dict[str, Any]) -> None:
        if self._event_cb is None:
            return
        result = self._event_cb(payload)
        if asyncio.iscoroutine(result):
            await result

    def _emit_sync(self, payload: Dict[str, Any]) -> None:
        """Synchronous emit shim used by emit_pressure_event (scheduler callback)."""
        if self._event_cb is None:
            return
        result = self._event_cb(payload)
        # If the callback returns a coroutine we can't await it here; schedule it.
        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(result)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Introspection                                                         #
    # ------------------------------------------------------------------ #

    def status_report(self) -> Dict[str, Any]:
        stats = self._dep_engine.stats(self.nodes)
        return {
            "graph_id": self.graph_id,
            "goal": self.goal,
            "stats": stats,
            "nodes": [n.to_summary() for n in self.nodes],
        }
