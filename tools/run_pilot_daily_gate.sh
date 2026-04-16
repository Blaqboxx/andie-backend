#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/jamai-jamison/valhalla/.venv/bin/python}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
SAMPLES_FILE="${SAMPLES_FILE:-storage/pilot/pilot_samples.jsonl}"
LOOKBACK_HOURS="${LOOKBACK_HOURS:-24}"
OUT_DIR="${OUT_DIR:-storage/pilot/daily_checks}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --samples-file)
      SAMPLES_FILE="$2"
      shift 2
      ;;
    --lookback-hours)
      LOOKBACK_HOURS="$2"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

cd "$REPO_ROOT" || exit 1
mkdir -p "$OUT_DIR"

ts="$(date -u +"%Y%m%dT%H%M%SZ")"
tmp_file="$OUT_DIR/${ts}.json.tmp"
final_file="$OUT_DIR/${ts}.json"

set +e
"$PYTHON_BIN" tools/pilot_daily_check.py \
  --base-url "$BASE_URL" \
  --samples-file "$SAMPLES_FILE" \
  --lookback-hours "$LOOKBACK_HOURS" \
  > "$tmp_file"
status=$?
set -e

mv "$tmp_file" "$final_file"
echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") status=$status result=$final_file"

exit "$status"
