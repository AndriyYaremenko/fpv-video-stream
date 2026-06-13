#!/usr/bin/env bash
# add-device.sh — wrapper around bin/add-device.js; loads .env then runs node.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec node bin/add-device.js "$@"
