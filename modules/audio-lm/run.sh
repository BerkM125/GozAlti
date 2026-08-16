#!/usr/bin/env bash
# audio-lm — voice confirmation loop. Everything machine-side lives here.
#   ./run.sh start | stop | restart | test | status | logs
# Stdlib python only: no venv, no model download, nothing to install.
set -uo pipefail
cd "$(dirname "$0")"
PORT="${AUDIO_PORT:-8050}"
PIDF="/tmp/audio-lm.pid"
LOG="/tmp/audio-lm.log"

start() {
  stop >/dev/null 2>&1
  AUDIO_PORT="$PORT" ESCALATION_URL="${ESCALATION_URL:-}" \
    nohup python3 service.py >"$LOG" 2>&1 &
  echo $! > "$PIDF"
  for i in $(seq 1 20); do
    curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && {
      curl -s "http://127.0.0.1:$PORT/health" | python3 -m json.tool; return 0; }
    sleep 0.5
  done
  echo "failed to start; log:"; tail -20 "$LOG"; exit 1
}
stop() { [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null && echo stopped || echo "not running"; rm -f "$PIDF"; }
case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop >/dev/null 2>&1; start ;;
  test) python3 test_dialogue.py ;;
  logs) tail -f "$LOG" ;;
  status) curl -s -m 3 "http://127.0.0.1:$PORT/health" | python3 -m json.tool 2>/dev/null || echo "not responding on :$PORT" ;;
  *) sed -n 2,5p "$0"; exit 1 ;;
esac
