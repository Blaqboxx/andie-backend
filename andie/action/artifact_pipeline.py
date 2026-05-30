from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("andie.artifact")

try:
    from andie_backend.andie.memory.cognitive_state import record_episode as _record_episode
except Exception:
    _record_episode = None


def _resolve_workspace_root() -> Path:
    candidates = [
        os.environ.get("ANDIE_ARTIFACT_ROOT"),
        "/app/workspace/artifacts",
        str(Path.cwd() / "artifacts"),
        str(Path(tempfile.gettempdir()) / "andie-artifacts"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate)
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            continue
    raise RuntimeError("unable_to_create_artifact_workspace")


WORKSPACE_ROOT = _resolve_workspace_root()


def _preprocess_raw(raw: str) -> str:
    """Fix mistral JSON quirks: nested backtick strings, JS comments."""
    raw = re.sub(r"//[^\n]*", "", raw)

    def bt_to_dq(m):
        inner = m.group(1)
        inner = inner.replace("\\", "\\\\")
        inner = inner.replace('"', '\\"')
        inner = "\\n".join(inner.splitlines())
        return '"' + inner + '"'

    raw = re.sub(r"`(\$\{[^}]*\})`", bt_to_dq, raw)
    raw = re.sub(r"`([^`]*)`", bt_to_dq, raw, flags=re.DOTALL)
    return raw


def _extract_json(raw: str) -> dict | None:
    """Extract JSON from LLM output - handles code fences, leading prose."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except Exception:
            pass

    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except Exception:
                        break
    return None


async def run_artifact_pipeline(target: str, ollama_chat_fn, system_prompt: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", target.lower())[:40].strip("-")
    workspace = WORKSPACE_ROOT / f"{slug}-{job_id}"
    workspace.mkdir(parents=True, exist_ok=True)

    plan_prompt = (
        "Output ONLY a valid JSON object. No text before or after it.\n"
        "STRICT JSON RULES:\n"
        "- Strings must use double quotes only. NO backticks.\n"
        "- Escape every newline inside values as \\n\n"
        "- Escape every double-quote inside values as \\\"\n"
        "- No JavaScript comments (// or /* */)\n"
        "- No trailing commas\n\n"
        "Build this: "
        + target
        + "\n\n"
        'JSON format: {"project_name":"name","description":"one sentence",'
        '"run_command":"how to run",'
        '"files":[{"path":"file.ext","content":"full escaped file content"}]}\n\n'
        "Write complete working code. Every newline in content MUST be \\n"
    )

    result = await ollama_chat_fn(
        messages=[{"role": "user", "content": plan_prompt}],
        system=system_prompt,
    )
    raw = result.get("response", "")

    debug_log = workspace / "_llm_raw.txt"
    debug_log.write_text(raw, encoding="utf-8")
    logger.info("[artifact] job=%s raw_len=%d", job_id, len(raw))

    processed = _preprocess_raw(raw)
    manifest = _extract_json(processed)

    if not manifest or "files" not in manifest:
        logger.warning("[artifact] job=%s JSON parse failed. raw[:300]=%r", job_id, raw[:300])
        return {
            "status": "error",
            "intent": "ARTIFACT_BUILD",
            "job_id": job_id,
            "workspace": str(workspace),
            "error": "LLM did not return a valid file manifest",
            "debug_log": str(debug_log),
            "raw_preview": raw[:300],
        }

    written = []
    for f in manifest.get("files", []):
        rel = f.get("path", "output.txt")
        file_path = workspace / rel
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.get("content", ""), encoding="utf-8")
        written.append(rel)

    meta = workspace / "ANDIE_BUILD.json"
    meta.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "target": target,
                "built_at": datetime.utcnow().isoformat(),
                "files": written,
                "run_command": manifest.get("run_command", ""),
                "description": manifest.get("description", ""),
            },
            indent=2,
        )
    )

    return {
        "status": "ok",
        "intent": "ARTIFACT_BUILD",
        "job_id": job_id,
        "workspace": str(workspace),
        "project_name": manifest.get("project_name", slug),
        "files": written,
        "run_command": manifest.get("run_command", ""),
        "description": manifest.get("description", ""),
        "latency_ms": result.get("latency_ms", 0),
    }
