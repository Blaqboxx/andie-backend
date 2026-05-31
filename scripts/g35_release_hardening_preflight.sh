#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ANDIE_BASE_URL:-http://127.0.0.1:8000}"
ACADEMY_ENDPOINT="${ANDIE_G35_ACADEMY_ENDPOINT:-http://blaqtower:8000}"
INFERENCE_ENDPOINT="${ANDIE_G35_INFERENCE_ENDPOINT:-http://blaqtower3:8000}"
LOCAL_NODE_ID="${ANDIE_A2A_LOCAL_NODE_ID:-}"
INSTITUTION_NODES="${ANDIE_A2A_INSTITUTION_NODES:-}"
NODE_ENDPOINTS="${ANDIE_A2A_NODE_ENDPOINTS:-}"

ok() {
  echo "OK: $*"
}

fail() {
  echo "FAIL: $*"
  FAILED=1
}

probe_http() {
  local url="$1"
  local label="$2"
  local code
  code="$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "$url" || true)"
  if [[ "$code" == "200" ]]; then
    ok "$label reachable ($url)"
  else
    fail "$label unreachable ($url, status=$code)"
  fi
}

probe_ssh() {
  local host="$1"
  local user="$2"
  if ssh -o BatchMode=yes -o ConnectTimeout=5 "$user@$host" 'echo ok' >/dev/null 2>&1; then
    ok "ssh access works for $user@$host"
  else
    fail "ssh access failed for $user@$host"
  fi
}

FAILED=0

echo "== G3.5 Hardening Preflight =="
echo "base_url=$BASE_URL"

a2a_mode="${ANDIE_A2A_TRANSPORT_MODE:-}"
if [[ "$a2a_mode" == "inter_node" ]]; then
  ok "ANDIE_A2A_TRANSPORT_MODE=inter_node"
else
  fail "ANDIE_A2A_TRANSPORT_MODE must be inter_node (current='$a2a_mode')"
fi

if [[ -n "$LOCAL_NODE_ID" ]]; then
  ok "ANDIE_A2A_LOCAL_NODE_ID present"
else
  fail "ANDIE_A2A_LOCAL_NODE_ID missing"
fi

if [[ -n "$INSTITUTION_NODES" ]]; then
  ok "ANDIE_A2A_INSTITUTION_NODES present"
else
  fail "ANDIE_A2A_INSTITUTION_NODES missing"
fi

if [[ -n "$NODE_ENDPOINTS" ]]; then
  ok "ANDIE_A2A_NODE_ENDPOINTS present"
else
  fail "ANDIE_A2A_NODE_ENDPOINTS missing"
fi

probe_http "$BASE_URL/a2a/deployment/topology" "coordinator a2a topology"
probe_http "$BASE_URL/health" "coordinator health"
probe_http "$ACADEMY_ENDPOINT/health" "academy node api"
probe_http "$INFERENCE_ENDPOINT/health" "inference node api"

SSH_USER="${ANDIE_G35_SSH_USER:-jamai-jamison}"
probe_ssh "blaqtower" "$SSH_USER"
probe_ssh "blaqtower3" "$SSH_USER"

if [[ "$FAILED" -ne 0 ]]; then
  echo "\nPreflight status: FAIL"
  echo "Unblock requirements:"
  echo "1) Bring up coordinator API at $BASE_URL"
  echo "2) Bring up academy API at $ACADEMY_ENDPOINT"
  echo "3) Bring up inference API at $INFERENCE_ENDPOINT"
  echo "4) Export inter-node env vars on coordinator"
  echo "5) Restore non-interactive SSH access for $SSH_USER@blaqtower and $SSH_USER@blaqtower3"
  exit 2
fi

echo "\nPreflight status: PASS"
exit 0
