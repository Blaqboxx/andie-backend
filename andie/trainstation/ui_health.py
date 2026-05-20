"""
ui_health.py — Frontend visibility validation engine.

Checks (in order of confidence):
  1. HTTP reachability + status code
  2. HTML shell integrity (doctype, root div, script tag, CSS tag)
  3. JS bundle size sanity (not truncated, not empty)
  4. CSS static analysis — scan for visibility-killing rules
  5. React mount signal — scan bundle for known mount identifiers
  6. Composite visibility score

Returns a structured health dict suitable for registry + Trainstation decisions.
"""
from __future__ import annotations
import re
import time
import socket
import urllib.request
import urllib.error
import subprocess
from pathlib import Path
from typing import Any

UI_URL       = "http://localhost:5173"
UI_HOST_IP   = "http://192.168.50.183:5173"   # reachable from backend container
DIST_PATH    = Path("/app/andie-ui/dist")
CONTAINER_UI = "andie-ui"

# Score thresholds
SCORE_HEALTHY  = 80
SCORE_DEGRADED = 50


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 5) -> tuple[int, str]:
    """Return (status_code, body_text). status=0 on connection failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ANDIE-HealthBot/1.0"})
        r   = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def _docker_exec(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Run a command inside the andie-ui container."""
    try:
        r = subprocess.run(
            ["docker", "exec", CONTAINER_UI, "sh", "-c", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _read_local(path: Path, max_bytes: int = 500_000) -> str:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return ""


# ── Individual Checks ─────────────────────────────────────────────────────────

def check_http() -> dict:
    t0 = time.monotonic()
    code, body = _fetch(UI_HOST_IP)
    latency_ms = round((time.monotonic() - t0) * 1000)
    if code == 0:
        return {"check": "http", "status": "failed", "detail": "connection refused", "latency_ms": 0}
    if code >= 500:
        return {"check": "http", "status": "failed", "detail": f"HTTP {code}", "latency_ms": latency_ms}
    return {"check": "http", "status": "ok", "detail": f"HTTP {code}", "latency_ms": latency_ms, "_body": body}


def check_html_shell(body: str) -> dict:
    """Verify the served HTML has the expected SPA shell structure."""
    issues = []
    if "<!doctype html" not in body.lower():
        issues.append("missing DOCTYPE")
    if '<div id="root">' not in body and "<div id='root'>" not in body:
        issues.append("root div missing")
    if 'src="/assets/' not in body and "src='/assets/" not in body:
        issues.append("JS bundle tag missing")
    if 'href="/assets/' not in body and "href='/assets/" not in body:
        issues.append("CSS bundle tag missing")

    if issues:
        return {"check": "html_shell", "status": "failed", "detail": "; ".join(issues)}

    # Extract bundle filenames
    js_match  = re.search(r'src="/assets/([^"]+\.js)"',  body)
    css_match = re.search(r'href="/assets/([^"]+\.css)"', body)
    return {
        "check": "html_shell", "status": "ok", "detail": "shell intact",
        "js_bundle":  js_match.group(1)  if js_match  else None,
        "css_bundle": css_match.group(1) if css_match else None,
    }


def check_bundles(js_name: str | None, css_name: str | None) -> dict:
    """Verify JS and CSS bundle files exist on disk and have reasonable sizes."""
    issues = []
    sizes  = {}

    for name, kind, min_bytes in [
        (js_name,  "js",  50_000),
        (css_name, "css", 5_000),
    ]:
        if not name:
            issues.append(f"{kind} bundle name unknown")
            continue
        path = DIST_PATH / "assets" / name
        if not path.exists():
            issues.append(f"{name} not found on disk")
            continue
        sz = path.stat().st_size
        sizes[kind] = sz
        if sz < min_bytes:
            issues.append(f"{name} suspiciously small ({sz}B < {min_bytes}B)")

    if issues:
        return {"check": "bundles", "status": "failed", "detail": "; ".join(issues), "sizes": sizes}
    return {"check": "bundles", "status": "ok", "detail": f"js={sizes.get('js',0)//1024}KB css={sizes.get('css',0)//1024}KB", "sizes": sizes}


def check_css_sanity(css_name: str | None) -> dict:
    """Scan the CSS bundle for visibility-killing rules."""
    if not css_name:
        return {"check": "css_sanity", "status": "unknown", "detail": "css bundle name not found"}

    path = DIST_PATH / "assets" / css_name
    css  = _read_local(path, max_bytes=200_000)
    if not css:
        return {"check": "css_sanity", "status": "failed", "detail": "could not read CSS bundle"}

    suspects = []

    # Check #root / body for visibility killers
    root_block = ""
    for pat in [r"#root\s*\{([^}]+)\}", r"body\s*\{([^}]+)\}"]:
        m = re.search(pat, css)
        if m:
            root_block += m.group(1)

    checks = [
        (r"opacity\s*:\s*0(?!\.)(?:[^;]|$)", "opacity:0 on root/body"),
        (r"display\s*:\s*none",               "display:none on root/body"),
        (r"visibility\s*:\s*hidden",          "visibility:hidden on root/body"),
        (r"height\s*:\s*0(?:px)?(?:\s|;|$)", "height:0 on root/body"),
        (r"overflow\s*:\s*hidden",            "overflow:hidden on root/body"),
    ]
    for pattern, label in checks:
        if re.search(pattern, root_block, re.IGNORECASE):
            suspects.append(label)

    # Global z-index trap
    if re.search(r"z-index\s*:\s*-\d{3,}", css):
        suspects.append("large negative z-index detected globally")

    if suspects:
        return {"check": "css_sanity", "status": "degraded", "detail": "; ".join(suspects), "suspects": suspects}
    return {"check": "css_sanity", "status": "ok", "detail": f"no visibility killers ({len(css)//1024}KB scanned)"}


def check_react_mount_signal(js_name: str | None) -> dict:
    """Scan the JS bundle for React mount indicators."""
    if not js_name:
        return {"check": "react_mount", "status": "unknown", "detail": "js bundle name not found"}

    path = DIST_PATH / "assets" / js_name
    # Only need a portion — mount call is near the start
    js = _read_local(path, max_bytes=50_000)
    if not js:
        return {"check": "react_mount", "status": "failed", "detail": "could not read JS bundle"}

    signals = {
        "createRoot":     "createRoot" in js,
        "ReactDOM":       "ReactDOM"   in js,
        "hydrateRoot":    "hydrateRoot" in js,
        "root_div":       "root" in js,
    }

    found = [k for k, v in signals.items() if v]
    # Vite minification removes createRoot literal — ReactDOM presence is sufficient
    if "createRoot" in found or "hydrateRoot" in found or "ReactDOM" in found:
        return {"check": "react_mount", "status": "ok", "detail": f"React mount signals: {', '.join(found)}"}
    if found:
        return {"check": "react_mount", "status": "degraded", "detail": f"partial signals only: {', '.join(found)}"}
    return {"check": "react_mount", "status": "failed", "detail": "no React mount signals in bundle — build may be corrupt"}


def check_container_running() -> dict:
    """Verify the andie-ui container is running."""
    rc, out = _docker_exec("echo alive")
    if rc == 0 and "alive" in out:
        return {"check": "container", "status": "ok", "detail": "andie-ui container exec ok"}
    # Fallback: HTTP check — UI reachable means container is running
    code, _ = _fetch(UI_HOST_IP, timeout=3)
    if code > 0:
        return {"check": "container", "status": "ok", "detail": "container up (HTTP verified)"}
    return {"check": "container", "status": "failed", "detail": "container unreachable via exec and HTTP"}


def check_dist_freshness() -> dict:
    """Check if dist/ was built recently vs source."""
    dist_index = DIST_PATH / "index.html"
    if not dist_index.exists():
        return {"check": "dist_freshness", "status": "failed", "detail": "dist/index.html missing — rebuild required"}

    import os
    dist_mtime = dist_index.stat().st_mtime
    age_minutes = round((time.time() - dist_mtime) / 60)

    # Check if any src/ files are newer than dist
    src_path = DIST_PATH.parent / "src"
    stale_files = []
    if src_path.exists():
        for p in src_path.rglob("*.jsx"):
            if p.stat().st_mtime > dist_mtime:
                stale_files.append(p.name)
            if len(stale_files) >= 5:
                break

    if stale_files:
        return {
            "check": "dist_freshness", "status": "degraded",
            "detail": f"dist is {age_minutes}min old; {len(stale_files)} src files newer: {stale_files[:3]}",
            "stale_files": stale_files, "age_minutes": age_minutes
        }
    return {"check": "dist_freshness", "status": "ok", "detail": f"dist built {age_minutes}min ago — current", "age_minutes": age_minutes}


# ── Composite Score ───────────────────────────────────────────────────────────

_WEIGHTS = {
    "http":           25,
    "container":      15,
    "html_shell":     15,
    "bundles":        15,
    "react_mount":    15,
    "css_sanity":     10,
    "dist_freshness":  5,
}

def _score(checks: list[dict]) -> int:
    total_weight = sum(_WEIGHTS.values())
    earned       = 0
    check_map    = {c["check"]: c["status"] for c in checks}
    for name, weight in _WEIGHTS.items():
        status = check_map.get(name, "unknown")
        if status == "ok":
            earned += weight
        elif status == "degraded":
            earned += weight // 2
    return round(earned * 100 / total_weight)


# ── Public API ────────────────────────────────────────────────────────────────

def run_checks() -> dict:
    """Full UI health sweep. Returns structured result with visibility_score."""
    checks = []

    # HTTP (needed for downstream checks)
    http_result = check_http()
    checks.append({k: v for k, v in http_result.items() if k != "_body"})
    body = http_result.get("_body", "")

    # Container
    checks.append(check_container_running())

    # HTML shell + bundle names
    shell = check_html_shell(body)
    checks.append(shell)
    js_name  = shell.get("js_bundle")
    css_name = shell.get("css_bundle")

    # Bundle files
    checks.append(check_bundles(js_name, css_name))

    # React mount signal
    checks.append(check_react_mount_signal(js_name))

    # CSS sanity
    checks.append(check_css_sanity(css_name))

    # Dist freshness
    checks.append(check_dist_freshness())

    # Composite score
    score = _score(checks)
    failures  = [c for c in checks if c["status"] == "failed"]
    degraded  = [c for c in checks if c["status"] == "degraded"]

    if score >= SCORE_HEALTHY:
        overall = "visible"
    elif score >= SCORE_DEGRADED:
        overall = "degraded"
    else:
        overall = "blank"

    return {
        "overall":          overall,
        "visibility_score": score,
        "blank_screen":     overall == "blank",
        "mounted":          any(c["check"] == "react_mount" and c["status"] == "ok" for c in checks),
        "dom_nodes":        1 if ('<div id="root">' in body) else 0,
        "checks":           checks,
        "failures":         failures,
        "degraded":         degraded,
        "timestamp":        time.time(),
    }


if __name__ == "__main__":
    import json
    result = run_checks()
    print(json.dumps(result, indent=2))
