from __future__ import annotations

import asyncio
import os
import resource
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExecutionResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration": round(self.duration, 3),
            "timed_out": self.timed_out,
        }


class BuildSandbox:
    """Controlled per-job workspace for autonomous builds."""

    def __init__(self, job_id: str, keep_on_failure: bool = True) -> None:
        self.job_id = str(job_id)
        self.keep_on_failure = keep_on_failure
        self.workspace = Path("storage") / "builds" / self.job_id
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.executions: list[ExecutionResult] = []
        self.written_files: list[str] = []

    def _safe_path(self, rel_path: str) -> Path:
        target = (self.workspace / rel_path).resolve()
        workspace_root = self.workspace.resolve()
        if workspace_root not in target.parents and target != workspace_root:
            raise ValueError(f"Path escapes workspace: {rel_path}")
        return target

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        target.write_text(text, encoding="utf-8")
        rel = str(target.relative_to(self.workspace.resolve()))
        if rel not in self.written_files:
            self.written_files.append(rel)
        return {
            "path": rel,
            "bytes": len(text.encode("utf-8")),
            "status": "written",
        }

    def read_file(self, path: str) -> str:
        target = self._safe_path(path)
        return target.read_text(encoding="utf-8")

    def list_files(self) -> list[str]:
        files: list[str] = []
        ignored_dirs = {"venv", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
        if not self.workspace.exists():
            return files
        for p in self.workspace.rglob("*"):
            rel_parts = p.relative_to(self.workspace).parts
            if any(part in ignored_dirs for part in rel_parts):
                continue
            if p.is_file():
                files.append(str(p.relative_to(self.workspace)))
            if len(files) >= 500:
                break
        files.sort()
        return files

    def _apply_limits(self) -> None:
        # CPU seconds for the child process tree.
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        # Virtual memory cap: 1.5 GB.
        memory_bytes = int(1.5 * 1024 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        # File descriptor cap.
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))

    def _run_blocking(self, command: str, timeout: int) -> ExecutionResult:
        start = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=self._apply_limits,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            result = ExecutionResult(
                command=command,
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=int(completed.returncode),
                duration=time.monotonic() - start,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
            stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
            result = ExecutionResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=124,
                duration=time.monotonic() - start,
                timed_out=True,
            )
        self.executions.append(result)
        return result

    async def execute(self, command: str, timeout: int = 30) -> dict[str, Any]:
        result = await asyncio.to_thread(self._run_blocking, command, int(timeout))
        return result.as_dict()

    def cleanup(self, persist: bool) -> None:
        if persist:
            return
        if not self.workspace.exists():
            return
        for p in sorted(self.workspace.rglob("*"), key=lambda i: len(str(i)), reverse=True):
            if p.is_file() or p.is_symlink():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        self.workspace.rmdir()

    def get_result_summary(self) -> dict[str, Any]:
        last = self.executions[-1].as_dict() if self.executions else None
        return {
            "job_id": self.job_id,
            "workspace": str(self.workspace),
            "files": self.list_files(),
            "written_files": list(self.written_files),
            "execution_count": len(self.executions),
            "last_execution": last,
            "success": bool(last and last.get("exit_code") == 0),
        }
