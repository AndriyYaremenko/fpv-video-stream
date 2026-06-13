#!/usr/bin/env bash
# compose-add-device.sh — add a device on the Docker (wg-easy netns) deployment.
# Generates creds, updates devices.yml, regenerates mediamtx.yml, prints the Pi push command,
# then restarts mediamtx (new config) and the dashboard (so the new tile appears).
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -lt 1 ]; then
  echo 'Usage: ./compose-add-device.sh <device-id> "<friendly name>" "<location>"'
  exit 2
fi

docker compose run --rm \
  -e DEVICES_FILE=/app/devices.yml \
  -e MEDIAMTX_CONFIG=/runtime/mediamtx.yml \
  config-gen node bin/add-device.js "$@"

docker compose restart mediamtx dashboard
echo "Done. mediamtx + dashboard restarted with the new device."
