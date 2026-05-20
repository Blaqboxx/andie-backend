"""
andie/audio/ssml/parser.py
Lightweight SSML parser — converts SSML markup into TTS directives
and also plain text for engines that don't natively support SSML.

Supported tags:
  <speak>               root element
  <break time="500ms"/> silence pause
  <emphasis level="strong|moderate|reduced">
  <prosody rate="fast|slow|+20%" pitch="+5Hz" volume="loud">
  <say-as interpret-as="characters|digits|date|time|currency">
  <voice name="...">    voice override

Usage:
    from andie_backend.andie.audio.ssml.parser import parse, to_plain_text
    directives = parse(ssml_string)
    plain = to_plain_text(ssml_string)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


# ── Directive types ───────────────────────────────────────────────────────────
# Each directive is a dict:
#   {"type": "speak",    "text": str, "voice": str, "rate": str, "pitch": str}
#   {"type": "break",    "time_ms": int}
#   {"type": "emphasis", "text": str, "level": str}
#   {"type": "say-as",   "text": str, "interpret_as": str}


def _parse_time(value: str) -> int:
    """Parse '500ms' or '1s' to milliseconds."""
    value = value.strip()
    if value.endswith("ms"):
        return int(value[:-2])
    if value.endswith("s"):
        return int(float(value[:-1]) * 1000)
    return 0


def _node_to_directives(node: ET.Element, inherited: dict) -> list[dict]:
    """Recursively convert an XML node tree to a flat list of directives."""
    tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
    ctx = dict(inherited)

    # Collect prosody overrides
    if tag == "prosody":
        for attr in ("rate", "pitch", "volume"):
            v = node.get(attr)
            if v:
                ctx[attr] = v

    directives: list[dict] = []

    # Leading text in this node
    if node.text and node.text.strip():
        text = node.text.strip()
        if tag == "emphasis":
            directives.append({
                "type":  "emphasis",
                "text":  text,
                "level": node.get("level", "moderate"),
                **ctx,
            })
        elif tag == "say-as":
            directives.append({
                "type":         "say-as",
                "text":         text,
                "interpret_as": node.get("interpret-as", "text"),
                **ctx,
            })
        else:
            directives.append({"type": "speak", "text": text, **ctx})

    # Children
    for child in node:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if child_tag == "break":
            time_ms = _parse_time(child.get("time", "0"))
            directives.append({"type": "break", "time_ms": time_ms})
        else:
            directives.extend(_node_to_directives(child, ctx))

        # Tail text after child closing tag
        if child.tail and child.tail.strip():
            directives.append({"type": "speak", "text": child.tail.strip(), **ctx})

    return directives


def parse(ssml: str) -> list[dict]:
    """
    Parse SSML string into a list of directives.
    Falls back to a single speak directive if parsing fails.
    """
    ssml = ssml.strip()
    if not ssml.startswith("<"):
        return [{"type": "speak", "text": ssml}]

    # Wrap in speak if not already
    if not ssml.startswith("<speak"):
        ssml = f"<speak>{ssml}</speak>"

    # Strip namespace declarations for simplicity
    ssml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", ssml)

    try:
        root = ET.fromstring(ssml)
    except ET.ParseError:
        # Strip all tags, return plain
        plain = re.sub(r"<[^>]+>", " ", ssml).strip()
        return [{"type": "speak", "text": plain}]

    ctx: dict[str, Any] = {}
    # Top-level voice
    voice = root.get("voice") or root.find("voice")
    if isinstance(voice, str):
        ctx["voice"] = voice

    return _node_to_directives(root, ctx)


def to_plain_text(ssml: str) -> str:
    """Strip all SSML tags and return clean plain text."""
    directives = parse(ssml)
    parts = []
    for d in directives:
        if d["type"] == "break":
            parts.append(" ")
        elif "text" in d:
            parts.append(d["text"])
    return " ".join(p for p in parts if p.strip())


def directives_to_tts_params(directives: list[dict]) -> dict:
    """
    Flatten directives into a single TTS call parameter set.
    Returns {text, voice, rate, pitch}.
    Combined text respects pauses (represented as '... ').
    """
    parts = []
    voice = None
    rate  = None
    pitch = None

    for d in directives:
        if d["type"] == "break":
            ms = d.get("time_ms", 0)
            # Approximate silence as ellipsis pause in SSML-aware engines
            if ms > 0:
                parts.append("...")
        elif "text" in d:
            parts.append(d["text"])
            if not voice and d.get("voice"):
                voice = d["voice"]
            if not rate and d.get("rate"):
                rate = d["rate"]
            if not pitch and d.get("pitch"):
                pitch = d["pitch"]

    return {
        "text":  " ".join(p for p in parts if p),
        "voice": voice,
        "rate":  rate,
        "pitch": pitch,
    }
