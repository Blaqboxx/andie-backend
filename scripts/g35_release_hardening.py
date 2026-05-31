#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: Dict[str, Any]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> Optional[datetime]:
    value = str(ts or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _request_json(method: str, url: str, payload: Optional[Dict[str, Any]], timeout_s: float) -> Tuple[int, Dict[str, Any]]:
    body: Optional[bytes] = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = Request(
        url=url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=timeout_s) as response:
            status_code = int(getattr(response, "status", 200))
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return status_code, (parsed if isinstance(parsed, dict) else {})
    except HTTPError as exc:
        raw = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"error": raw or str(exc)}
        return int(exc.code), (parsed if isinstance(parsed, dict) else {"error": str(parsed)})
    except URLError as exc:
        return 0, {"error": f"url_error:{exc}"}


def _check_topology(topology: Dict[str, Any], expected: Dict[str, str], require_inter_node: bool) -> CheckResult:
    mode = str(topology.get("mode") or "")
    institution_nodes = dict(topology.get("institution_nodes") or {})
    mismatches: List[Dict[str, Any]] = []
    for institution_id, expected_node in expected.items():
        actual = str(institution_nodes.get(institution_id) or "")
        if actual != expected_node:
            mismatches.append({
                "institution_id": institution_id,
                "expected": expected_node,
                "actual": actual,
            })

    mode_ok = (mode == "inter_node") if require_inter_node else bool(mode)
    return CheckResult(
        name="deployment_topology_matches_contract",
        passed=mode_ok and not mismatches,
        details={
            "mode": mode,
            "require_inter_node": require_inter_node,
            "mismatches": mismatches,
            "known_nodes": list(topology.get("known_nodes") or []),
        },
    )


def _check_replay_continuity(
    *,
    workflow_name: str,
    workflow: Dict[str, Any],
    expected_edges: List[Tuple[str, str]],
) -> List[CheckResult]:
    replay = dict(workflow.get("replay") or {})
    items = [dict(item) for item in list(replay.get("items") or []) if isinstance(item, dict)]

    continuity_ok = bool(items)
    session_id = str(workflow.get("session_id") or "")
    correlation_id = str(workflow.get("correlation_id") or "")

    for item in items:
        if str(item.get("session_id") or "") != session_id:
            continuity_ok = False
        if str(item.get("correlation_id") or "") != correlation_id:
            continuity_ok = False

    edge_projection = [(str(i.get("sender") or ""), str(i.get("receiver") or "")) for i in items]
    edge_ok = edge_projection == expected_edges

    deployment_metadata_ok = bool(items)
    audit_shape_ok = bool(items)
    for item in items:
        if not isinstance(item.get("deployment"), dict):
            deployment_metadata_ok = False
        if not isinstance(item.get("transport"), dict):
            deployment_metadata_ok = False
        for required_field in ["sender", "receiver", "message_type", "status", "request", "response"]:
            if required_field not in item:
                audit_shape_ok = False

    first_created = _parse_iso(str(items[0].get("created_at") if items else ""))
    last_created = _parse_iso(str(items[-1].get("created_at") if items else ""))
    e2e_ms: Optional[int] = None
    if first_created is not None and last_created is not None:
        e2e_ms = max(0, int((last_created - first_created).total_seconds() * 1000))

    return [
        CheckResult(
            name=f"{workflow_name}_workflow_completed",
            passed=bool(workflow.get("completed")) and str(workflow.get("status")) == "completed",
            details={"status": workflow.get("status"), "completed": workflow.get("completed")},
        ),
        CheckResult(
            name=f"{workflow_name}_replay_session_and_correlation_continuity",
            passed=continuity_ok,
            details={
                "session_id": session_id,
                "correlation_id": correlation_id,
                "item_count": len(items),
            },
        ),
        CheckResult(
            name=f"{workflow_name}_replay_edge_sequence",
            passed=edge_ok,
            details={"expected_edges": expected_edges, "actual_edges": edge_projection},
        ),
        CheckResult(
            name=f"{workflow_name}_deployment_metadata_continuity",
            passed=deployment_metadata_ok,
            details={"item_count": len(items)},
        ),
        CheckResult(
            name=f"{workflow_name}_audit_shape_completeness",
            passed=audit_shape_ok,
            details={"item_count": len(items)},
        ),
        CheckResult(
            name=f"{workflow_name}_cross_node_latency_observed",
            passed=e2e_ms is not None,
            details={"end_to_end_ms": e2e_ms},
        ),
    ]


def _check_outage_behavior(
    base_url: str,
    *,
    outage_mode: str,
    timeout_s: float,
    session_id: str,
) -> CheckResult:
    if outage_mode == "none":
        return CheckResult(
            name="academy_outage_behavior",
            passed=True,
            details={"mode": "none", "skipped": True},
        )

    payload: Dict[str, Any] = {
        "session_id": session_id,
        "topic": "g35_release_hardening_outage_probe",
        "timeout_seconds": 2,
    }
    if outage_mode == "simulate":
        payload["simulate_timeout"] = True

    status_code, body = _request_json(
        "POST",
        f"{base_url}/a2a/workflows/workshop-academy-exchange",
        payload,
        timeout_s,
    )
    workflow = dict(body.get("workflow") or {})
    replay = dict(workflow.get("replay") or {})
    items = [dict(item) for item in list(replay.get("items") or []) if isinstance(item, dict)]

    passed = (
        status_code == 200
        and str(workflow.get("status") or "") == "timed_out"
        and bool(replay.get("found"))
        and len(items) >= 1
    )
    return CheckResult(
        name="academy_outage_behavior",
        passed=passed,
        details={
            "mode": outage_mode,
            "http_status": status_code,
            "workflow_status": workflow.get("status"),
            "replay_found": replay.get("found"),
            "replay_count": replay.get("count"),
            "error_code": (items[0].get("error_code") if items else None),
            "failure_stage": workflow.get("failure_stage"),
        },
    )


def run(args: argparse.Namespace) -> int:
    base_url = str(args.base_url).rstrip("/")
    expected_topology = {
        "workshop": "blaqtower2",
        "academy": "blaqtower1",
        "inference": "blaqtower3",
    }

    checks: List[CheckResult] = []
    probe_started_at = _iso_now()

    topo_status, topo_body = _request_json(
        "GET",
        f"{base_url}/a2a/deployment/topology",
        None,
        float(args.timeout_s),
    )
    topology = dict(topo_body.get("topology") or {})
    checks.append(
        CheckResult(
            name="deployment_topology_endpoint_available",
            passed=topo_status == 200 and bool(topology),
            details={"http_status": topo_status, "mode": topology.get("mode")},
        )
    )
    checks.append(
        _check_topology(
            topology,
            expected=expected_topology,
            require_inter_node=bool(args.require_inter_node_mode),
        )
    )

    if topo_status == 200:
        for institution_id in ["workshop", "academy", "inference"]:
            route_status, route_body = _request_json(
                "GET",
                f"{base_url}/a2a/deployment/topology?institution_id={institution_id}",
                None,
                float(args.timeout_s),
            )
            route = dict((route_body.get("topology") or {}).get("route") or {})
            checks.append(
                CheckResult(
                    name=f"route_lookup_{institution_id}",
                    passed=(route_status == 200 and str(route.get("assigned_node") or "") == expected_topology[institution_id]),
                    details={
                        "http_status": route_status,
                        "institution_id": institution_id,
                        "assigned_node": route.get("assigned_node"),
                    },
                )
            )

    session_prefix = str(args.session_prefix).strip()
    wa_session = f"{session_prefix}_wa"
    wai_session = f"{session_prefix}_wai"

    wa_status, wa_body = _request_json(
        "POST",
        f"{base_url}/a2a/workflows/workshop-academy-exchange",
        {
            "session_id": wa_session,
            "topic": str(args.topic),
            "timeout_seconds": int(args.timeout_seconds),
        },
        float(args.timeout_s),
    )
    wa_workflow = dict(wa_body.get("workflow") or {})
    checks.append(
        CheckResult(
            name="workshop_academy_workflow_endpoint_available",
            passed=wa_status == 200,
            details={"http_status": wa_status, "status": wa_workflow.get("status")},
        )
    )
    checks.extend(
        _check_replay_continuity(
            workflow_name="workshop_academy",
            workflow=wa_workflow,
            expected_edges=[("workshop", "academy"), ("academy", "workshop")],
        )
    )

    wai_status, wai_body = _request_json(
        "POST",
        f"{base_url}/a2a/workflows/workshop-academy-inference-exchange",
        {
            "session_id": wai_session,
            "topic": str(args.topic),
            "timeout_seconds": int(args.timeout_seconds),
        },
        float(args.timeout_s),
    )
    wai_workflow = dict(wai_body.get("workflow") or {})
    checks.append(
        CheckResult(
            name="workshop_academy_inference_workflow_endpoint_available",
            passed=wai_status == 200,
            details={"http_status": wai_status, "status": wai_workflow.get("status")},
        )
    )
    checks.extend(
        _check_replay_continuity(
            workflow_name="workshop_academy_inference",
            workflow=wai_workflow,
            expected_edges=[("workshop", "academy"), ("academy", "inference")],
        )
    )

    checks.append(
        _check_outage_behavior(
            base_url,
            outage_mode=str(args.outage_mode),
            timeout_s=float(args.timeout_s),
            session_id=f"{session_prefix}_outage",
        )
    )

    required_checks = [item for item in checks if item.details.get("skipped") is not True]
    passed = all(item.passed for item in required_checks)

    report = {
        "status": ("pass" if passed else "fail"),
        "probe_started_at": probe_started_at,
        "probe_finished_at": _iso_now(),
        "operator": str(args.operator),
        "base_url": base_url,
        "outage_mode": str(args.outage_mode),
        "checks": [asdict(item) for item in checks],
    }

    output_path = Path(str(args.output)).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps({"status": report["status"], "output": str(output_path)}, sort_keys=True))
    return 0 if passed else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G3.5 release hardening evidence harness")
    parser.add_argument("--base-url", default=os.environ.get("ANDIE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--session-prefix", default=f"g35_hardening_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--topic", default=os.environ.get("ANDIE_G35_TOPIC", "release hardening continuity"))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("ANDIE_G35_WORKFLOW_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--timeout-s", type=float, default=float(os.environ.get("ANDIE_G35_HTTP_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--operator", default=os.environ.get("USER", "unknown_operator"))
    parser.add_argument("--outage-mode", choices=["none", "simulate", "live"], default=os.environ.get("ANDIE_G35_OUTAGE_MODE", "none"))
    parser.add_argument("--require-inter-node-mode", action="store_true", help="Fail if deployment topology mode is not inter_node.")
    parser.add_argument(
        "--output",
        default=os.environ.get(
            "ANDIE_G35_HARDENING_REPORT_PATH",
            f"artifacts/g35/release_hardening_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
