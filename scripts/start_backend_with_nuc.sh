#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${BACKEND_DIR}/.." && pwd)"

CLI_NUC_HOST="${ANDIE_NUC_WORKER_API_HOST:-}"
CLI_NUC_PORT="${ANDIE_NUC_WORKER_API_PORT:-}"
CLI_LOCAL_NODE_ID="${ANDIE_LOCAL_NODE_ID:-}"
CLI_CLUSTER_SECRET="${ANDIE_CLUSTER_SHARED_SECRET:-}"

if [[ -f "${ROOT_DIR}/.andie-dev.env" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "${ROOT_DIR}/.andie-dev.env"
	set +a
fi

if [[ -n "${CLI_NUC_HOST}" ]]; then
	ANDIE_NUC_WORKER_API_HOST="${CLI_NUC_HOST}"
fi
if [[ -n "${CLI_NUC_PORT}" ]]; then
	ANDIE_NUC_WORKER_API_PORT="${CLI_NUC_PORT}"
fi
if [[ -n "${CLI_LOCAL_NODE_ID}" ]]; then
	ANDIE_LOCAL_NODE_ID="${CLI_LOCAL_NODE_ID}"
fi
if [[ -n "${CLI_CLUSTER_SECRET}" ]]; then
	ANDIE_CLUSTER_SHARED_SECRET="${CLI_CLUSTER_SECRET}"
fi

# Override these at runtime if needed:
# ANDIE_NUC_WORKER_API_HOST=192.168.50.200 ANDIE_NUC_WORKER_API_PORT=9000 ./scripts/start_backend_with_nuc.sh
export ANDIE_NUC_WORKER_API_HOST="${ANDIE_NUC_WORKER_API_HOST:-192.168.50.138}"
export ANDIE_NUC_WORKER_API_PORT="${ANDIE_NUC_WORKER_API_PORT:-9000}"
export ANDIE_LOCAL_NODE_ID="${ANDIE_LOCAL_NODE_ID:-thinkpad}"
export ANDIE_CLUSTER_SHARED_SECRET="${ANDIE_CLUSTER_SHARED_SECRET:-andie-dev-shared-secret}"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
BACKEND_PORT="${ANDIE_THINKPAD_BACKEND_PORT:-${ANDIE_BACKEND_PORT:-8000}}"
BACKEND_LOG="${ANDIE_THINKPAD_BACKEND_LOG:-${BACKEND_DIR}/logs/andie-backend.log}"

mkdir -p "$(dirname "${BACKEND_LOG}")"

pkill -f "uvicorn interfaces.api.main:app" >/dev/null 2>&1 || true

cd "${BACKEND_DIR}"
nohup "${PYTHON_BIN}" -m uvicorn interfaces.api.main:app --reload --host 0.0.0.0 --port "${BACKEND_PORT}" > "${BACKEND_LOG}" 2>&1 < /dev/null &

echo "Backend launch requested"
echo "  NUC host: ${ANDIE_NUC_WORKER_API_HOST}"
echo "  NUC port: ${ANDIE_NUC_WORKER_API_PORT}"
echo "  Local node id: ${ANDIE_LOCAL_NODE_ID}"
echo "  Backend port: ${BACKEND_PORT}"
echo "  Log: ${BACKEND_LOG}"