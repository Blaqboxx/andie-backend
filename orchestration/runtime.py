"""
Canonical Orchestrator Runtime with Governed Tool Execution and Model Routing

This module centralizes:
  - Orchestration lifecycle (startup/shutdown)
  - Autonomy loop management
  - Governed tool execution with permission/timeout boundaries
  - LLM model selection and routing
  - Observable state and metrics
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

import psutil

from .tool_governance import ToolExecutionGovernor
from .model_router import ModelRouter


class OrchestratorState(Enum):
    """Canonical orchestration lifecycle states."""
    UNINITIALIZED = "uninitialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class OrchestrationMetrics:
    """Observable orchestration runtime metrics."""
    startup_timestamp: Optional[str] = None
    shutdown_timestamp: Optional[str] = None
    uptime_seconds: float = 0.0
    autonomy_loop_iterations: int = 0
    autonomy_decisions_made: int = 0
    tasks_processed: int = 0
    tools_executed: int = 0
    models_invoked: int = 0
    last_error: Optional[str] = None
    cpu_percent_during_run: float = 0.0
    memory_percent_during_run: float = 0.0
    background_threads_active: int = 0


@dataclass
class OrchestratorConfig:
    """Configuration for orchestrator runtime."""
    # Autonomy loop
    autonomy_enabled: bool = True
    autonomy_interval_seconds: float = 5.0
    autonomy_max_iterations: int = 1000
    autonomy_decision_cooldown_seconds: float = 15.0
    
    # Safety limits
    max_recovery_attempts: int = 3
    shutdown_timeout_seconds: float = 10.0
    
    # Tool execution governance
    tool_default_timeout_seconds: float = 5.0
    tool_audit_max_records: int = 500
    
    # Model routing
    model_router_enabled: bool = True
    default_model: str = "mistral:latest"
    
    # Observability
    emit_metrics_interval_seconds: float = 30.0


class OrchestratorRuntime:
    """
    Canonical owner of all orchestration lifecycle.
    
    This class centralizes:
      - Background thread management
      - Autonomy loop lifecycle
      - Task orchestration with governed tools
      - LLM model selection and routing
      - Graceful shutdown
      - Observable state
      - Governance enforcement
    
    Usage:
      runtime = OrchestratorRuntime(config)
      await runtime.startup()  # In FastAPI lifespan
      await runtime.shutdown() # In FastAPI lifespan
    """
    
    def __init__(self, config: Optional[OrchestratorConfig] = None):
        self.config = config or OrchestratorConfig()
        self.state = OrchestratorState.UNINITIALIZED
        self.metrics = OrchestrationMetrics()
        
        # Thread management
        self._autonomy_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._startup_complete_event = threading.Event()
        
        # Lifecycle hooks
        self._on_startup_complete: Optional[Callable[[], None]] = None
        self._on_shutdown: Optional[Callable[[], None]] = None
        
        # Autonomy state
        self._autonomy_running = False
        self._autonomy_iterations = 0
        self._autonomy_last_decision_time = 0.0
        self._autonomy_decisions = 0
        
        # Governed tool execution
        self._tool_governor = ToolExecutionGovernor(
            default_timeout_seconds=self.config.tool_default_timeout_seconds,
            max_audit_records=self.config.tool_audit_max_records,
        )
        self._register_builtin_tools()
        
        # Model routing (Phase 1: Option A)
        self._model_router = ModelRouter(default_model=self.config.default_model)
    
    def _register_builtin_tools(self) -> None:
        """Register baseline internal tools with explicit permissions."""

        def _echo_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
            return {"echo": payload}

        def _sleep_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
            seconds = float(payload.get("seconds", 0.0))
            seconds = max(0.0, min(seconds, 60.0))
            time.sleep(seconds)
            return {"slept_seconds": seconds}

        self._tool_governor.register_tool(
            "echo",
            _echo_tool,
            description="Echo payload for validation and diagnostics",
            timeout_seconds=2.0,
            allowed_roles={"system", "operator", "validator"},
        )
        self._tool_governor.register_tool(
            "sleep",
            _sleep_tool,
            description="Controlled delay tool for timeout validation",
            timeout_seconds=3.0,
            allowed_roles={"system", "validator"},
        )
    
    def _utc_now(self) -> str:
        """Current UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()
    
    async def startup(self) -> None:
        """
        Startup orchestrator runtime.
        Called during FastAPI lifespan startup.
        """
        with self._state_lock:
            if self.state != OrchestratorState.UNINITIALIZED:
                raise RuntimeError(f"Cannot startup from state: {self.state}")
            self.state = OrchestratorState.STARTING
        
        self.metrics.startup_timestamp = self._utc_now()
        print("[OrchestratorRuntime] Startup initiated")
        
        try:
            # Initialize model router
            if self.config.model_router_enabled:
                self._model_router.load_available_models()
                print("[OrchestratorRuntime] Model router initialized")
            
            # Start autonomy loop if enabled
            if self.config.autonomy_enabled:
                self._stop_event.clear()
                self._autonomy_thread = threading.Thread(
                    target=self._autonomy_loop_worker,
                    name="orchestrator-autonomy",
                    daemon=False  # Explicit lifecycle management
                )
                self._autonomy_thread.start()
                print("[OrchestratorRuntime] Autonomy thread spawned")
            
            # Wait for threads to initialize
            self._startup_complete_event.wait(timeout=5.0)
            
            with self._state_lock:
                self.state = OrchestratorState.RUNNING
                self._autonomy_running = True
            
            print("[OrchestratorRuntime] Startup complete → RUNNING")
            if self._on_startup_complete:
                self._on_startup_complete()
        
        except Exception as e:
            with self._state_lock:
                self.state = OrchestratorState.FAILED
                self.metrics.last_error = str(e)
            print(f"[OrchestratorRuntime] Startup failed: {e}")
            raise
    
    async def shutdown(self, timeout_seconds: Optional[float] = None) -> None:
        """
        Graceful shutdown of orchestrator runtime.
        Called during FastAPI lifespan shutdown.
        """
        timeout = timeout_seconds or self.config.shutdown_timeout_seconds
        
        with self._state_lock:
            if self.state not in (OrchestratorState.RUNNING, OrchestratorState.FAILED):
                print(f"[OrchestratorRuntime] Shutdown called in state {self.state}, skipping")
                return
            self.state = OrchestratorState.STOPPING
        
        print(f"[OrchestratorRuntime] Shutdown initiated (timeout={timeout}s)")
        
        # Signal all threads to stop
        self._stop_event.set()
        self._autonomy_running = False
        
        # Wait for threads with timeout
        start_time = time.time()
        if self._autonomy_thread and self._autonomy_thread.is_alive():
            self._autonomy_thread.join(timeout=timeout)
            elapsed = time.time() - start_time
            if self._autonomy_thread.is_alive():
                print(f"[OrchestratorRuntime] WARNING: Autonomy thread did not stop after {elapsed}s")
            else:
                print(f"[OrchestratorRuntime] Autonomy thread stopped after {elapsed:.1f}s")
        
        self.metrics.shutdown_timestamp = self._utc_now()
        
        with self._state_lock:
            self.state = OrchestratorState.STOPPED
        
        self._tool_governor.shutdown(wait=False)
        
        if self._on_shutdown:
            self._on_shutdown()
        
        print("[OrchestratorRuntime] Shutdown complete → STOPPED")
    
    def _autonomy_loop_worker(self) -> None:
        """Autonomy background worker (runs in thread)."""
        print("[OrchestratorRuntime.autonomy] Thread started")
        self._startup_complete_event.set()
        
        try:
            while not self._stop_event.is_set():
                with self._state_lock:
                    if not self._autonomy_running:
                        break
                    self._autonomy_iterations += 1
                    iteration = self._autonomy_iterations
                
                # Safety: max iterations
                if iteration > self.config.autonomy_max_iterations:
                    print(f"[OrchestratorRuntime.autonomy] Max iterations reached ({iteration})")
                    with self._state_lock:
                        self._autonomy_running = False
                    break
                
                # Core autonomy logic (placeholder for now)
                try:
                    self._autonomy_step(iteration)
                except Exception as e:
                    print(f"[OrchestratorRuntime.autonomy] Step {iteration} error: {e}")
                    with self._state_lock:
                        self.metrics.last_error = str(e)
                
                # Polite sleep
                time.sleep(self.config.autonomy_interval_seconds)
        
        finally:
            print("[OrchestratorRuntime.autonomy] Thread exiting")
    
    def _autonomy_step(self, iteration: int) -> None:
        """Single autonomy loop iteration."""
        # Placeholder for actual autonomy logic
        if iteration % 10 == 0:
            self.metrics.cpu_percent_during_run = psutil.cpu_percent(interval=None)
            self.metrics.memory_percent_during_run = psutil.virtual_memory().percent
            self.metrics.autonomy_loop_iterations = iteration
    
    def get_status(self) -> Dict[str, Any]:
        """Get current orchestration status."""
        with self._state_lock:
            uptime = 0.0
            if self.metrics.startup_timestamp:
                startup = datetime.fromisoformat(self.metrics.startup_timestamp)
                now = datetime.now(timezone.utc)
                uptime = (now - startup).total_seconds()
        
        return {
            "state": self.state.value,
            "uptime_seconds": uptime,
            "metrics": {
                "autonomy_iterations": self.metrics.autonomy_loop_iterations,
                "autonomy_decisions": self.metrics.autonomy_decisions_made,
                "tasks_processed": self.metrics.tasks_processed,
                "tools_executed": self.metrics.tools_executed,
                "models_invoked": self.metrics.models_invoked,
                "cpu_percent": self.metrics.cpu_percent_during_run,
                "memory_percent": self.metrics.memory_percent_during_run,
                "background_threads": self.metrics.background_threads_active,
                "last_error": self.metrics.last_error,
            },
            "config": {
                "autonomy_enabled": self.config.autonomy_enabled,
                "autonomy_interval_seconds": self.config.autonomy_interval_seconds,
                "shutdown_timeout_seconds": self.config.shutdown_timeout_seconds,
                "tool_default_timeout_seconds": self.config.tool_default_timeout_seconds,
                "model_router_enabled": self.config.model_router_enabled,
                "distributed_inference_enabled": True,
            }
        }
    
    def is_running(self) -> bool:
        """Check if orchestrator is in RUNNING state."""
        with self._state_lock:
            return self.state == OrchestratorState.RUNNING
    
    def is_healthy(self) -> bool:
        """Check if orchestrator is healthy (not FAILED)."""
        with self._state_lock:
            return self.state != OrchestratorState.FAILED
    
    def register_on_startup_complete(self, callback: Callable[[], None]) -> None:
        """Register callback to invoke on startup complete."""
        self._on_startup_complete = callback
    
    def register_on_shutdown(self, callback: Callable[[], None]) -> None:
        """Register callback to invoke on shutdown."""
        self._on_shutdown = callback
    
    # ─── Tool Execution Governance ───────────────────────────────────────
    
    def register_tool(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Any],
        *,
        description: str = "",
        timeout_seconds: Optional[float] = None,
        allowed_roles: Optional[set[str]] = None,
        enabled: bool = True,
    ) -> None:
        """Register a governed tool in explicit runtime registry."""
        self._tool_governor.register_tool(
            name,
            handler,
            description=description,
            timeout_seconds=timeout_seconds,
            allowed_roles=allowed_roles,
            enabled=enabled,
        )

    def execute_tool(
        self,
        tool_name: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        actor: str = "system",
        role: str = "system",
        timeout_seconds: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute tool with permission checks, timeout, and audit logging."""
        self.metrics.tools_executed += 1
        return self._tool_governor.execute(
            tool_name,
            payload,
            actor=actor,
            role=role,
            timeout_seconds=timeout_seconds,
            correlation_id=correlation_id,
        )

    def list_registered_tools(self) -> list[Dict[str, Any]]:
        """List explicit governed tool registry."""
        return self._tool_governor.list_tools()

    def tool_audit_log(self, limit: int = 50) -> list[Dict[str, Any]]:
        """Return recent tool audit records."""
        return self._tool_governor.recent_audit_records(limit=limit)
    
    # ─── Model Routing (Phase 1: Option A) ───────────────────────────────
    
    def select_model(
        self,
        task_type: str,
        task_description: str = "",
        actor: str = "system",
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Select appropriate LLM model for task.
        
        Returns:
            {
                "model": "model_name:tag",
                "reason": "why this model was selected",
                "estimated_tokens": N,
                "route": "local|remote",
            }
        """
        self.metrics.models_invoked += 1
        selection = self._model_router.select(
            task_type=task_type,
            task_description=task_description,
            actor=actor,
            correlation_id=correlation_id,
        )
        return selection
    
    def list_available_models(self) -> Dict[str, Any]:
        """List models available for routing."""
        return self._model_router.get_model_info()
    
    def model_routing_policy(self) -> Dict[str, Any]:
        """Get current model routing policy."""
        return self._model_router.get_policy()

    def model_telemetry(self) -> Dict[str, Any]:
        """Get model endpoint health and latency telemetry."""
        return self._model_router.get_telemetry()
