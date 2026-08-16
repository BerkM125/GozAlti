#!/usr/bin/env bash
# Serve the offline viewer from the box. No build step, no network calls from the page.
#
#   ./serve_viewer.sh          # port 8099
#   PORT=9000 ./serve_viewer.sh
#
# Binds 0.0.0.0 so it is reachable over the venue LAN and over the tailnet. The page
# itself makes no external requests at all, so once it has loaded it keeps working
# even when the wifi drops mid-demo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8099}"
cd "$HERE/viewer"
[ -f data/index.json ] || { echo "no data/index.json yet — run video.py first"; exit 1; }
echo "viewer:  http://$(hostname).local:$PORT/"
echo "tailnet: http://100.106.143.38:$PORT/"
echo "clips:   $(python3 -c 'import json;print(len(json.load(open("data/index.json"))["clips"]))')"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
