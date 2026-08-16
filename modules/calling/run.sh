#!/usr/bin/env bash
# modules/calling — fan-out alert service. Stdlib only, no pip install.
#
# Arm whichever channels you have. Nothing here is required; unset channels report
# "not configured" instead of failing.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
exec python3 service.py
