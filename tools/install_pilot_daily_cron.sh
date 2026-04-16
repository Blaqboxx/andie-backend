#!/usr/bin/env bash
set -euo pipefail

SCHEDULE="${1:-5 7 * * *}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO_ROOT/tools/run_pilot_daily_gate.sh"

MARKER_BEGIN="# BEGIN ANDIE_PILOT_DAILY_GATE"
MARKER_END="# END ANDIE_PILOT_DAILY_GATE"
JOB_LINE="$SCHEDULE cd $REPO_ROOT && /bin/bash $RUNNER --base-url http://127.0.0.1:8000 --samples-file storage/pilot/pilot_samples.jsonl --lookback-hours 24 >> storage/pilot/daily_gate.cron.log 2>&1"

existing="$(crontab -l 2>/dev/null || true)"
cleaned="$(printf "%s\n" "$existing" | awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
  $0 == b {skip=1; next}
  $0 == e {skip=0; next}
  skip == 0 {print}
')"

new_crontab="$cleaned
$MARKER_BEGIN
$JOB_LINE
$MARKER_END
"

printf "%s\n" "$new_crontab" | crontab -

echo "Installed ANDIE pilot daily gate cron job:"
echo "  $JOB_LINE"
echo "Current crontab snippet:"
printf "%s\n" "$MARKER_BEGIN"
printf "%s\n" "$JOB_LINE"
printf "%s\n" "$MARKER_END"
