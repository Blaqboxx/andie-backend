"""
Governed tool execution for orchestration runtime.

Provides:
  - Explicit tool registry
  - Permission boundary enforcement
  - Per-tool/default timeout enforcement
  - Structured audit logging
  - Failure isolation
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4


class ToolGovernanceError(Exception):
    """Base error for governed tool execution."""


class ToolNotFoundError(ToolGovernanceError):
    """Raised when a tool is not registered."""


class ToolPermissionError(ToolGovernanceError):
    """Raised when actor role is not allowed to execute tool."""


class ToolTimeoutError(ToolGovernanceError):
    """Raised when tool exceeds configured timeout."""


@dataclass
class ToolDefinition:
    """Static registration record for a governed tool."""

    name: str
    handler: Callable[[Dict[str, Any]], Any]
    description: str = ""
    timeout_seconds: Optional[float] = None
    allowed_roles: Set[str] = field(default_factory=lambda: {"system"})
    enabled: bool = True


@dataclass
class ToolExecutionRecord:
    """Structured execution record for auditability."""

    execution_id: str
    timestamp_utc: str
    tool_name: str
    actor: str
    role: str
    status: str
    timeout_seconds: float
    duration_ms: int
    correlation_id: Optional[str]
    error: Optional[str] = None


class ToolExecutionGovernor:
    """Registry + governed execution boundary for tools."""

    def __init__(self, default_timeout_seconds: float = 5.0, max_audit_records: int = 500):
        self.default_timeout_seconds = default_timeout_seconds
        self.max_audit_records = max_audit_records
        self._registry: Dict[str, ToolDefinition] = {}
        self._audit_log: List[ToolExecutionRecord] = []
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-governor")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def register_tool(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Any],
        *,
        description: str = "",
        timeout_seconds: Optional[float] = None,
        allowed_roles: Optional[Set[str]] = None,
        enabled: bool = True,
    ) -> None:
        with self._lock:
            self._registry[name] = ToolDefinition(
                name=name,
                handler=handler,
                description=description,
                timeout_seconds=timeout_seconds,
                allowed_roles=allowed_roles or {"system"},
                enabled=enabled,
            )

    def list_tools(self) -> List[Dict[str, Any]]:
        with self._lock:
            tools = []
            for name in sorted(self._registry.keys()):
                tool = self._registry[name]
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "timeout_seconds": tool.timeout_seconds,
                        "allowed_roles": sorted(tool.allowed_roles),
                        "enabled": tool.enabled,
                    }
                )
            return tools

    def recent_audit_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [record.__dict__ for record in self._audit_log[-max(1, limit) :]]

    def _append_audit(self, record: ToolExecutionRecord) -> None:
        with self._lock:
            self._audit_log.append(record)
            if len(self._audit_log) > self.max_audit_records:
                self._audit_log = self._audit_log[-self.max_audit_records :]

    def execute(
        self,
        tool_name: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        actor: str = "system",
        role: str = "system",
        timeout_seconds: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = payload or {}
        execution_id = str(uuid4())
        started = datetime.now(timezone.utc)

        with self._lock:
            tool = self._registry.get(tool_name)

        if tool is None:
            raise ToolNotFoundError(f"tool_not_found:{tool_name}")
        if not tool.enabled:
            raise ToolPermissionError(f"tool_disabled:{tool_name}")
        if role not in tool.allowed_roles:
            raise ToolPermissionError(f"permission_denied:{tool_name}:{role}")

        effective_timeout = timeout_seconds or tool.timeout_seconds or self.default_timeout_seconds

        status = "success"
        error = None
        output: Any = None

        try:
            future = self._executor.submit(tool.handler, payload)
            output = future.result(timeout=effective_timeout)
        except FutureTimeoutError:
            status = "timeout"
            error = f"tool_execution_timeout:{tool_name}:{effective_timeout}"
            future.cancel()
            raise ToolTimeoutError(error)
        except Exception as exc:
            status = "failed"
            error = f"tool_execution_failed:{tool_name}:{exc}"
            output = {
                "error": str(exc),
                "trace": traceback.format_exc(limit=3),
            }
        finally:
            ended = datetime.now(timezone.utc)
            duration_ms = int((ended - started).total_seconds() * 1000)
            self._append_audit(
                ToolExecutionRecord(
                    execution_id=execution_id,
                    timestamp_utc=self._utc_now(),
                    tool_name=tool_name,
                    actor=actor,
                    role=role,
                    status=status,
                    timeout_seconds=float(effective_timeout),
                    duration_ms=duration_ms,
                    correlation_id=correlation_id,
                    error=error,
                )
            )

        return {
            "execution_id": execution_id,
            "tool_name": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "result": output,
            "error": error,
        }
