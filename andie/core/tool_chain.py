"""
ANDIE Autonomous Tool Chain
─────────────────────────────
Lets ANDIE chain tool calls sequentially without a human in the loop.
Output from step N feeds into step N+1 via {{step_N.stdout}} / {{step_N.content}} tokens.

Architecture:
  BuildPlan (list[Step]) → ToolChain.run() → ChainResult

Each Step has:
  tool       : one of bash | write_file | read_file | http_get | http_post | llm_call
  args       : dict of tool-specific args — may contain {{step_N.X}} tokens
  on_error   : "abort" (default) | "continue" | "fix_and_retry"
  max_retries: int (default 0; only honoured when on_error="fix_and_retry")

The chain pauses and asks the LLM for a repair plan when on_error="fix_and_retry"
and a step fails. The LLM returns a replacement step dict or "skip".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]  # andie-backend/
MAX_CHAIN_STEPS = 50
MAX_OUTPUT_BYTES = 32_768  # 32 KB per step stdout


# ─────────────────────────────────────────────
# Step result
# ─────────────────────────────────────────────

class StepResult:
    def __init__(
        self,
        index: int,
        tool: str,
        success: bool,
        stdout: str = "",
        content: str = "",
        error: str = "",
        elapsed: float = 0.0,
    ):
        self.index = index
        self.tool = tool
        self.success = success
        self.stdout = stdout          # bash output / http body
        self.content = content        # file content / llm response
        self.error = error
        self.elapsed = elapsed

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "tool": self.tool,
            "success": self.success,
            "stdout": self.stdout,
            "content": self.content,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
        }


# ─────────────────────────────────────────────
# Token resolution
# ─────────────────────────────────────────────

def _resolve_tokens(value: Any, results: list[StepResult]) -> Any:
    """Replace {{step_N.field}} tokens in strings and nested dicts/lists."""
    if isinstance(value, str):
        def replace(m: re.Match) -> str:
            idx = int(m.group(1))
            field = m.group(2)
            if idx < len(results):
                return str(getattr(results[idx], field, ""))
            return m.group(0)  # leave unresolved
        return re.sub(r"\{\{step_(\d+)\.(\w+)\}\}", replace, value)
    if isinstance(value, dict):
        return {k: _resolve_tokens(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_tokens(item, results) for item in value]
    return value


# ─────────────────────────────────────────────
# Individual tool executors
# ─────────────────────────────────────────────

def _tool_bash(args: dict) -> StepResult:
    cmd = args.get("cmd", "")
    cwd = str(BASE_DIR / args["cwd"]) if "cwd" in args else str(BASE_DIR)
    timeout = int(args.get("timeout", 30))
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        stdout = (proc.stdout + proc.stderr)[:MAX_OUTPUT_BYTES]
        success = proc.returncode == 0
        return StepResult(
            index=-1, tool="bash",
            success=success,
            stdout=stdout,
            error="" if success else f"exit code {proc.returncode}",
            elapsed=time.monotonic() - t0,
        )
    except subprocess.TimeoutExpired:
        return StepResult(index=-1, tool="bash", success=False,
                          error=f"timeout after {timeout}s", elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="bash", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


def _tool_write_file(args: dict) -> StepResult:
    path = BASE_DIR / args["path"]
    content = args.get("content", "")
    t0 = time.monotonic()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return StepResult(index=-1, tool="write_file", success=True,
                          content=content, stdout=f"wrote {len(content)} bytes to {path}",
                          elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="write_file", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


def _tool_read_file(args: dict) -> StepResult:
    path = BASE_DIR / args["path"]
    t0 = time.monotonic()
    try:
        content = path.read_text(encoding="utf-8")[:MAX_OUTPUT_BYTES]
        return StepResult(index=-1, tool="read_file", success=True,
                          content=content, stdout=content,
                          elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="read_file", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


def _tool_http_get(args: dict) -> StepResult:
    import urllib.request
    url = args["url"]
    timeout = int(args.get("timeout", 15))
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
        return StepResult(index=-1, tool="http_get", success=True,
                          stdout=body, content=body, elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="http_get", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


def _tool_http_post(args: dict) -> StepResult:
    import urllib.request
    url = args["url"]
    payload = args.get("payload", {})
    timeout = int(args.get("timeout", 30))
    t0 = time.monotonic()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
        return StepResult(index=-1, tool="http_post", success=True,
                          stdout=body, content=body, elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="http_post", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


def _tool_llm_call(args: dict) -> StepResult:
    from andie_backend.brain.llm_router import call_llm
    prompt = args.get("prompt", "")
    system = args.get("system", "")
    t0 = time.monotonic()
    try:
        result = call_llm(prompt, system=system or None)
        text = ""
        if isinstance(result, dict):
            text = result.get("response") or result.get("result") or json.dumps(result)
        else:
            text = str(result)
        return StepResult(index=-1, tool="llm_call", success=True,
                          content=text, stdout=text, elapsed=time.monotonic() - t0)
    except Exception as exc:
        return StepResult(index=-1, tool="llm_call", success=False,
                          error=str(exc), elapsed=time.monotonic() - t0)


TOOL_MAP = {
    "bash": _tool_bash,
    "write_file": _tool_write_file,
    "read_file": _tool_read_file,
    "http_get": _tool_http_get,
    "http_post": _tool_http_post,
    "llm_call": _tool_llm_call,
}


# ─────────────────────────────────────────────
# Chain executor
# ─────────────────────────────────────────────

class ToolChain:
    """
    Execute a plan (list of step dicts) sequentially.
    Each result is accessible by later steps via {{step_N.stdout}} / {{step_N.content}}.
    """

    def __init__(self, plan: list[dict], task_description: str = ""):
        self.plan = plan[:MAX_CHAIN_STEPS]
        self.task_description = task_description
        self.results: list[StepResult] = []

    def _dispatch(self, tool: str, args: dict) -> StepResult:
        fn = TOOL_MAP.get(tool)
        if fn is None:
            return StepResult(index=-1, tool=tool, success=False,
                              error=f"Unknown tool '{tool}'")
        return fn(args)

    def _fix_step(self, failed_step: dict, error: str, context: list[dict]) -> dict | None:
        """Ask the LLM for a repair step. Returns a replacement step dict or None (=skip)."""
        try:
            from andie_backend.brain.llm_router import call_llm
            prompt = (
                f"A tool-chain step failed.\n\n"
                f"TASK: {self.task_description}\n\n"
                f"FAILED STEP:\n{json.dumps(failed_step, indent=2)}\n\n"
                f"ERROR: {error}\n\n"
                f"PREVIOUS RESULTS (last 3):\n"
                + "\n".join(json.dumps(r) for r in context[-3:])
                + "\n\nReturn a JSON object with a single replacement step "
                  '(same schema: {"tool":..., "args":...}) '
                  'or {"action": "skip"} to skip this step. '
                  "Return ONLY the JSON object, no explanation."
            )
            result = call_llm(prompt, system="You are an autonomous build repair engine.")
            text = ""
            if isinstance(result, dict):
                text = result.get("response") or result.get("result") or ""
            else:
                text = str(result)
            # strip markdown fences
            text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("`").strip()
            parsed = json.loads(text)
            if parsed.get("action") == "skip":
                return None
            return parsed
        except Exception:
            return None

    def run(self) -> dict:
        self.results = []
        aborted = False
        abort_reason = ""

        for i, raw_step in enumerate(self.plan):
            tool = raw_step.get("tool", "")
            raw_args = raw_step.get("args", {})
            on_error = raw_step.get("on_error", "abort")
            max_retries = int(raw_step.get("max_retries", 1))

            # resolve tokens from prior results
            args = _resolve_tokens(raw_args, self.results)

            result = self._dispatch(tool, args)
            result.index = i

            if not result.success:
                if on_error == "fix_and_retry":
                    for attempt in range(max_retries):
                        repair = self._fix_step(
                            raw_step,
                            result.error,
                            [r.as_dict() for r in self.results],
                        )
                        if repair is None:
                            # LLM said skip
                            result.success = True  # treat as non-fatal
                            result.stdout = "[skipped by repair engine]"
                            break
                        # re-run with repaired step
                        repair_args = _resolve_tokens(repair.get("args", {}), self.results)
                        result = self._dispatch(repair.get("tool", tool), repair_args)
                        result.index = i
                        if result.success:
                            break
                elif on_error == "continue":
                    pass  # keep going
                else:  # "abort"
                    self.results.append(result)
                    aborted = True
                    abort_reason = f"Step {i} ({tool}) failed: {result.error}"
                    break

            self.results.append(result)

        return {
            "task": self.task_description,
            "completed": not aborted,
            "aborted": aborted,
            "abort_reason": abort_reason,
            "steps_run": len(self.results),
            "steps": [r.as_dict() for r in self.results],
            "final_output": self.results[-1].stdout if self.results else "",
        }

    async def run_async(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run)


# ─────────────────────────────────────────────
# Plan generator (LLM-driven)
# ─────────────────────────────────────────────

def generate_build_plan(task: str, context: str = "") -> list[dict]:
    """
    Ask the LLM to produce a ToolChain plan for a build task.
    Returns a list of step dicts suitable for ToolChain(plan).
    """
    from andie_backend.brain.llm_router import call_llm

    schema = json.dumps({
        "tool": "bash | write_file | read_file | http_get | http_post | llm_call",
        "args": {
            "bash": {"cmd": "shell command", "cwd": "optional relative path", "timeout": 30},
            "write_file": {"path": "relative/path/to/file", "content": "file contents"},
            "read_file": {"path": "relative/path/to/file"},
            "http_get": {"url": "https://...", "timeout": 15},
            "http_post": {"url": "https://...", "payload": {}, "timeout": 30},
            "llm_call": {"prompt": "...", "system": "optional system prompt"},
        },
        "on_error": "abort | continue | fix_and_retry",
        "max_retries": 1,
    }, indent=2)

    prompt = (
        f"You are ANDIE, a full-spectrum systems builder.\n\n"
        f"TASK: {task}\n\n"
        + (f"CONTEXT:\n{context}\n\n" if context else "")
        + f"Generate a ToolChain execution plan as a JSON array of steps.\n"
          f"Each step schema:\n{schema}\n\n"
          f"Token interpolation: use {{{{step_N.stdout}}}} or {{{{step_N.content}}}} "
          f"to reference output from step N (0-indexed) in later steps.\n\n"
          f"Rules:\n"
          f"- Write actual file content, not placeholders\n"
          f"- Include validation steps (run tests, check output)\n"
          f"- Use on_error=fix_and_retry for critical steps\n"
          f"- Keep each step focused on one action\n"
          f"- Return ONLY the JSON array, no explanation\n"
    )

    result = call_llm(prompt, system="You are an autonomous build planner. Return only valid JSON.")
    text = ""
    if isinstance(result, dict):
        text = result.get("response") or result.get("result") or ""
    else:
        text = str(result)

    # strip markdown fences
    text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("`").strip()
    try:
        plan = json.loads(text)
        if isinstance(plan, list):
            return plan
    except Exception:
        pass
    return []
