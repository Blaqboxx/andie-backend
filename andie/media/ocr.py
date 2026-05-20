"""
andie/media/ocr.py
OCR extraction — accepts base64-encoded image, returns extracted text + heuristic events.
"""
from __future__ import annotations
import base64
import io
import re
import time

def _decode_image(data_url_or_b64: str):
    """Accepts data:image/...;base64,... or raw base64."""
    from PIL import Image
    if data_url_or_b64.startswith("data:"):
        b64 = data_url_or_b64.split(",", 1)[1]
    else:
        b64 = data_url_or_b64
    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw))

def extract(image_b64: str, mode: str = "screen") -> dict:
    """
    Run OCR on the supplied image.
    Returns:
      { text, lines, events, elapsed_ms, engine }
    """
    t0 = time.monotonic()
    try:
        import pytesseract
        img = _decode_image(image_b64)
        raw_text = pytesseract.image_to_string(img)
    except Exception as e:
        return {
            "text": "",
            "lines": [],
            "events": [],
            "elapsed_ms": 0,
            "engine": "none",
            "error": str(e)[:200],
        }

    elapsed = round((time.monotonic() - t0) * 1000)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    events = _detect_events(lines, mode)

    return {
        "text": raw_text.strip(),
        "lines": lines[:100],        # cap at 100 lines
        "events": events,
        "elapsed_ms": elapsed,
        "engine": "tesseract",
    }

# ── Vision event heuristics ──────────────────────────────────────────────────

_ERROR_PATTERNS = [
    re.compile(r'\b(error|exception|traceback|fatal|panic|segfault|killed)\b', re.I),
    re.compile(r'\b(errno|exit code [^0]|returncode)\b', re.I),
]
_WARNING_PATTERNS = [re.compile(r'\b(warning|warn|deprecated|caution)\b', re.I)]
_SUCCESS_PATTERNS = [re.compile(r'\b(success|passed|✓|done|complete|ok)\b', re.I)]
_DOCKER_PATTERNS = [re.compile(r'\b(container|docker|image|layer|push|pull)\b', re.I)]
_GIT_PATTERNS = [re.compile(r'\b(git|commit|push|merge|branch|diff)\b', re.I)]
_GPU_PATTERNS = [re.compile(r'\b(cuda|vram|gpu|nvml|nvidia|rocm|temperature)\b', re.I)]

def _detect_events(lines: list[str], mode: str) -> list[dict]:
    events = []
    full = " ".join(lines)

    for pat in _ERROR_PATTERNS:
        if pat.search(full):
            sample = next((l for l in lines if pat.search(l)), "")
            events.append({
                "type": "error_detected",
                "value": sample[:120],
                "confidence": 0.85,
                "severity": "high",
            })
            break

    for pat in _WARNING_PATTERNS:
        if pat.search(full):
            sample = next((l for l in lines if pat.search(l)), "")
            events.append({"type": "warning_detected", "value": sample[:120], "confidence": 0.75, "severity": "medium"})
            break

    for pat in _SUCCESS_PATTERNS:
        if pat.search(full):
            events.append({"type": "success_detected", "value": "", "confidence": 0.7, "severity": "info"})
            break

    if _DOCKER_PATTERNS[0].search(full):
        events.append({"type": "docker_activity", "value": "", "confidence": 0.8, "severity": "info"})

    if _GIT_PATTERNS[0].search(full):
        events.append({"type": "git_activity", "value": "", "confidence": 0.8, "severity": "info"})

    if _GPU_PATTERNS[0].search(full):
        events.append({"type": "gpu_reference", "value": "", "confidence": 0.8, "severity": "info"})

    return events
