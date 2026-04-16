#!/usr/bin/env bash
set -euo pipefail

ON_CALENDAR="${1:-*-*-* 07:05:00 UTC}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
SERVICE_NAME="andie-pilot-daily-gate.service"
TIMER_NAME="andie-pilot-daily-gate.timer"

mkdir -p "$USER_SYSTEMD_DIR"

cat > "$USER_SYSTEMD_DIR/$SERVICE_NAME" <<EOF
[Unit]
Description=ANDIE Pilot Daily Gate Check

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
ExecStart=/bin/bash $REPO_ROOT/tools/run_pilot_daily_gate.sh --base-url http://127.0.0.1:8000 --samples-file storage/pilot/pilot_samples.jsonl --lookback-hours 24
EOF

cat > "$USER_SYSTEMD_DIR/$TIMER_NAME" <<EOF
[Unit]
Description=Run ANDIE Pilot Daily Gate Check

[Timer]
OnCalendar=$ON_CALENDAR
Persistent=true
Unit=$SERVICE_NAME

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER_NAME"

echo "Installed and started $TIMER_NAME"
systemctl --user status "$TIMER_NAME" --no-pager -l || true
