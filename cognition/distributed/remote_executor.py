"""
Remote Executor — distributed task execution for STEP 9.

DistributedExecutor is the drop-in replacement for TaskGraph's ``executor``
callable.  It combines NodeScheduler + RemoteRunner to:

  1. Route each TaskNode to the best cluster node via NodeScheduler
  2. Execute the node's ``run_command`` on the selected node
  3. Return a standard ``{exit_code, stdout, stderr, status}`` dict that
     TaskGraph / EpistemicEngine can consume without modification

Execution backends (auto-selected per node):
  * Local — asyncio subprocess (no network hop)
  * Remote via asyncssh — fastest, full async SSH (requires asyncssh package)
  * Remote via openssh CLI — fallback using system ``ssh`` binary

The remote fallback chain ensures ANDIE can dispatch tasks even on systems
where the asyncssh package is not installed.

Emits events to the graph event bus when tasks are routed to remote nodes,
so dashboards can visualise distributed execution in real-time.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import socket
import time
from typing import Any, Callable, Dict, List, Optional

from .node_models import NodeState, RemoteResult, RoutingDecision
from .node_registry import NodeRegistry
from .node_scheduler import NodeScheduler

log = logging.getLogger(__name__)

_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_TIMEOUT = 120  # seconds

def _is_local_host(hostname: str) -> bool:
    """True when hostname resolves to this machine (loopback or own hostname)."""
    if hostname in _LOOPBACK_HOSTNAMES:
        return True
    try:
        own = socket.gethostname()
        own_fqdn = socket.getfqdn()
        return hostname in (own, own_fqdn)
    except Exception:
        return False




# ── Optional asyncssh ───────────────────────────────────────────────────
try:
    import asyncssh  # type: ignore[import]
    _ASYNCSSH_OK = True
except ImportError:
    asyncssh = None  # type: ignore[assignment]
    _ASYNCSSH_OK = False

EventCallback = Callable[[Dict[str, Any]], Any]


# ── Low-level execution helpers ─────────────────────────────────────────

async def _run_local(command: str, timeout: float = _DEFAULT_TIMEOUT) -> RemoteResult:
    """Execute a shell command on the local node via asyncio subprocess."""
    t0 = time.perf_counter()
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        latency = (time.perf_counter() - t0) * 1000
        code = proc.returncode or 0
        return RemoteResult(
            exit_code=code,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            status="success" if code == 0 else "error",
            node_id="local",
            hostname="localhost",
            latency_ms=round(latency, 2),
        )
    except asyncio.TimeoutError:
        return RemoteResult(
            exit_code=-1,
            stderr=f"TimeoutError: local command exceeded {timeout}s",
            status="error",
        )
    except Exception as exc:
        return RemoteResult(exit_code=-1, stderr=str(exc), status="error")


async def _run_ssh_asyncssh(
    node: NodeState,
    command: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> RemoteResult:
    """Execute via asyncssh (preferred remote backend)."""
    t0 = time.perf_counter()
    try:
        async with asyncio.wait_for(
            asyncssh.connect(
                node.hostname,
                port=node.port,
                username=node.ssh_user,
                known_hosts=None,
            ),
            timeout=10.0,
        ) as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=timeout,
            )
        latency = (time.perf_counter() - t0) * 1000
        code    = result.exit_status or 0
        return RemoteResult(
            exit_code=code,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            status="success" if code == 0 else "error",
            node_id=node.node_id,
            hostname=node.hostname,
            latency_ms=round(latency, 2),
        )
    except asyncio.TimeoutError:
        return RemoteResult(
            exit_code=-1,
            stderr=f"SSH timeout connecting to {node.hostname}",
            status="error",
            node_id=node.node_id,
            hostname=node.hostname,
        )
    except Exception as exc:
        return RemoteResult(
            exit_code=-1,
            stderr=f"asyncssh error: {exc}",
            status="error",
            node_id=node.node_id,
            hostname=node.hostname,
        )


async def _run_ssh_cli(
    node: NodeState,
    command: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> RemoteResult:
    """
    Fallback: execute via the system ``ssh`` binary.

    Requires the executing user to have passwordless SSH access to the
    remote node (key-based auth via ssh-agent or ~/.ssh/config).
    """
    t0 = time.perf_counter()
    ssh_cmd = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "
        f"-p {node.port} {node.ssh_user}@{node.hostname} "
        f"{shlex.quote(command)}"
    )
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout + 10,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout + 10)
        latency = (time.perf_counter() - t0) * 1000
        code    = proc.returncode or 0
        return RemoteResult(
            exit_code=code,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            status="success" if code == 0 else "error",
            node_id=node.node_id,
            hostname=node.hostname,
            latency_ms=round(latency, 2),
        )
    except asyncio.TimeoutError:
        return RemoteResult(
            exit_code=-1,
            stderr=f"SSH CLI timeout to {node.hostname}",
            status="error",
            node_id=node.node_id,
            hostname=node.hostname,
        )
    except Exception as exc:
        return RemoteResult(
            exit_code=-1,
            stderr=str(exc),
            status="error",
            node_id=node.node_id,
            hostname=node.hostname,
        )


# ── High-level executor ─────────────────────────────────────────────────

class DistributedExecutor:
    """
    TaskGraph-compatible executor that dispatches tasks across cluster nodes.

    Usage::

        registry    = NodeRegistry()
        executor    = DistributedExecutor(registry)

        # As a drop-in for TaskGraph:
        graph = TaskGraph(goal=..., nodes=..., scheduler=resource_scheduler)
        result = await graph.execute(executor=executor, node_timeout=120)

    Task routing is controlled by TaskNode.tags:
      ``"target:nuc-main"``         — hard pin to specific node
      ``"requires:gpu_inference"``  — routed to GPU-capable node
      ``"requires:ollama"``         — routed to Ollama-capable node
    """

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        event_cb: Optional[EventCallback] = None,
        postpone_llm: bool = False,
    ) -> None:
        self._registry  = registry
        self._scheduler = NodeScheduler(registry, postpone_llm=postpone_llm)
        self._event_cb  = event_cb

    # ------------------------------------------------------------------ #
    # Main callable (executor interface)                                   #
    # ------------------------------------------------------------------ #

    async def __call__(self, task_node: Any) -> Dict[str, Any]:
        """
        Execute ``task_node.run_command`` on the best available cluster node.

        Falls back to ``echo 'no command'`` if run_command is not set.
        Always returns a dict compatible with TaskGraph's executor contract.
        """
        from cognition.planning.task_models import TaskNode
        assert isinstance(task_node, TaskNode), "executor expects a TaskNode"

        command = task_node.run_command or f"echo 'task {task_node.id} has no run_command'"
        tags    = task_node.tags or []

        # ── Route ────────────────────────────────────────────────────
        decision = self._scheduler.route(task_node.id, tags)

        await self._emit({
            "event":    "task_routed",
            "task_id":  task_node.id,
            "node_id":  decision.selected_node_id,
            "hostname": decision.selected_hostname,
            "reason":   decision.reason,
            "is_local": decision.is_local,
            "is_fallback": decision.is_fallback,
        })

        # ── Execute ──────────────────────────────────────────────────
        node = self._registry.get(decision.selected_node_id)
        if node is None or decision.is_local or node.node_id == "local" or _is_local_host(node.hostname):
            result = await _run_local(command)
        elif _ASYNCSSH_OK:
            result = await _run_ssh_asyncssh(node, command)
        else:
            result = await _run_ssh_cli(node, command)

        await self._emit({
            "event":     "task_executed",
            "task_id":   task_node.id,
            "node_id":   result.node_id,
            "exit_code": result.exit_code,
            "latency_ms": result.latency_ms,
        })

        log.info(
            "DistributedExecutor: task '%s' → %s [exit=%d, %.0fms]",
            task_node.id,
            decision.selected_hostname,
            result.exit_code,
            result.latency_ms,
        )
        return result.to_dict()

    def set_postpone_llm(self, value: bool) -> None:
        """Propagate LLM-postpone flag from ResourceAwareScheduler."""
        self._scheduler.set_postpone_llm(value)

    # ------------------------------------------------------------------ #
    # Event emission                                                       #
    # ------------------------------------------------------------------ #

    async def _emit(self, payload: Dict[str, Any]) -> None:
        if self._event_cb is None:
            return
        result = self._event_cb(payload)
        if asyncio.iscoroutine(result):
            await result
