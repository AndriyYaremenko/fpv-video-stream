import logging
import os
import time

from collector import build_payload
from publisher import TelemetryPublisher

LOG = logging.getLogger("telemetry.main")


def load_env(env=None):
    env = os.environ if env is None else env
    return {
        "node_id": env.get("TELEM_NODE_ID", "bladerf"),
        "interval_s": int(env.get("TELEM_INTERVAL_S", "15")),
        "host": env.get("TELEM_MQTT_HOST", "10.8.0.1"),
        "port": int(env.get("TELEM_MQTT_PORT", "1883")),
        "user": env.get("MQTT_PUB_USER", "pub"),
        "password": env.get("MQTT_PUB_PASS", ""),
    }


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_env()
    pub = TelemetryPublisher(cfg["host"], cfg["port"], cfg["user"], cfg["password"], cfg["node_id"])
    # Retry the initial connect: over WireGuard the broker may not be reachable the instant the unit
    # starts at boot. paho auto-reconnects once the first connect succeeds, so we only guard connect().
    while True:
        try:
            pub.connect()
            break
        except Exception:
            LOG.warning("MQTT connect to %s:%s failed; retry in %ss",
                        cfg["host"], cfg["port"], cfg["interval_s"], exc_info=True)
            time.sleep(cfg["interval_s"])
    LOG.info("fpv-telemetry -> fpv/%s/telemetry every %ss", cfg["node_id"], cfg["interval_s"])
    try:
        while True:
            pub.publish(build_payload(cfg["node_id"]))
            time.sleep(cfg["interval_s"])
    except KeyboardInterrupt:
        pass
    finally:
        pub.close()


if __name__ == "__main__":
    main()
