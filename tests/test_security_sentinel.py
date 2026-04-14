import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

workspace_root = Path(__file__).resolve().parent.parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from interfaces.api.security_sentinel import authorize_worker_request, signed_headers, verify_signed_headers
from worker_api import app as worker_app


class SecuritySentinelTests(unittest.TestCase):
    def test_signed_headers_round_trip(self):
        payload = {"task": "heavy task", "context": "ctx", "params": {"priority": 1}}

        with patch("interfaces.api.security_sentinel.time.time", return_value=1000):
            headers = signed_headers(payload, caller="thinkpad", timestamp=1000)
            valid, reason, caller = verify_signed_headers(headers, payload)

        self.assertTrue(valid)
        self.assertEqual(reason, "ok")
        self.assertEqual(caller, "thinkpad")

    def test_authorize_worker_request_rejects_bad_signature(self):
        payload = {"task": "heavy task", "context": "ctx", "params": {}}
        headers = {
            "Authorization": "Bearer thinkpad:1000:bad",
            "X-ANDIE-Caller": "thinkpad",
            "X-ANDIE-Timestamp": "1000",
            "X-ANDIE-Signature": "bad",
        }

        with patch("interfaces.api.security_sentinel.time.time", return_value=1000):
            valid, reason, _ = authorize_worker_request(headers, payload)

        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_signature")


class WorkerEndpointSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(worker_app)
        self.audit_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.audit_dir.name) / "security-audit.log"
        self.old_audit = os.environ.get("ANDIE_SECURITY_AUDIT_LOG")
        os.environ["ANDIE_SECURITY_AUDIT_LOG"] = str(self.audit_path)

    def tearDown(self):
        if self.old_audit is None:
            os.environ.pop("ANDIE_SECURITY_AUDIT_LOG", None)
        else:
            os.environ["ANDIE_SECURITY_AUDIT_LOG"] = self.old_audit
        self.audit_dir.cleanup()

    def test_worker_execute_requires_signed_headers(self):
        response = self.client.post("/execute", json={"task": "do work", "context": "", "params": {}})

        self.assertEqual(response.status_code, 401)

    def test_worker_execute_accepts_signed_headers_and_logs_audit(self):
        payload = {"task": "do work", "context": "", "params": {}}
        with patch("interfaces.api.security_sentinel.time.time", return_value=1000):
            headers = signed_headers(payload, caller="thinkpad", timestamp=1000)
            response = self.client.post("/execute", json=payload, headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.audit_path.exists())
        audit_lines = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(any(item["event"] == "worker_auth_accepted" for item in audit_lines))
        self.assertTrue(any(item["event"] == "remote_execute_completed" for item in audit_lines))


if __name__ == "__main__":
    unittest.main()