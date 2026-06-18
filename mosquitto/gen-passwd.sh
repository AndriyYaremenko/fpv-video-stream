#!/usr/bin/env bash
# Generate mosquitto/passwd from the MQTT_*_USER/PASS in ../.env, using the mosquitto image.
# Run on the server (has docker) from the repo root: bash mosquitto/gen-passwd.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${MQTT_PUB_USER:?}" "${MQTT_PUB_PASS:?}" "${MQTT_SUB_USER:?}" "${MQTT_SUB_PASS:?}"
docker run --rm -v "$(pwd)/mosquitto:/m" eclipse-mosquitto:2 sh -c "
  mosquitto_passwd -b -c /m/passwd '$MQTT_PUB_USER' '$MQTT_PUB_PASS' &&
  mosquitto_passwd -b /m/passwd '$MQTT_SUB_USER' '$MQTT_SUB_PASS'
"
echo "wrote mosquitto/passwd (users: $MQTT_PUB_USER, $MQTT_SUB_USER)"
