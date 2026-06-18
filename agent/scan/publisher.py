import json
import logging
import time

LOG = logging.getLogger("scan.publisher")


def build_spectrum_frame(scanner_id, ts, band_id, low_mhz, high_mhz, psd):
    """Self-describing single-band spectrum frame (SP-A contract)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "bands": [{"id": band_id, "low_mhz": low_mhz, "high_mhz": high_mhz, "psd": psd}],
    }


def build_detection_payload(scanner_id, ts, detections, occupancy):
    """Detection event payload (SP-A contract)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "detections": [d.to_dict() for d in detections],
        "occupancy": occupancy,
    }


def _default_client_factory(client_id):
    # Lazy import: paho is only needed for a real connection, not for tests/builders.
    import paho.mqtt.client as mqtt
    # paho-mqtt 2.0 requires an explicit CallbackAPIVersion; VERSION1 keeps the
    # historical on_connect(client, userdata, flags, rc) signature used below.
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)


class MqttPublisher:
    QOS_SPECTRUM = 0
    QOS_DETECTION = 1
    QOS_STATUS = 1

    def __init__(self, host, port, user, password, scanner_id, keepalive=60, client_factory=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.scanner_id = scanner_id
        self.keepalive = keepalive
        self._factory = client_factory or _default_client_factory
        self._t_spectrum = f"fpv/{scanner_id}/spectrum"
        self._t_detection = f"fpv/{scanner_id}/detection"
        self._t_status = f"fpv/{scanner_id}/status"
        self._client = None

    def connect(self, ts):
        client = self._factory(f"scan-{self.scanner_id}")
        if self.user:
            client.username_pw_set(self.user, self.password)
        client.will_set(
            self._t_status, json.dumps({"online": False, "ts": ts}),
            qos=self.QOS_STATUS, retain=True,
        )
        client.on_connect = self._on_connect
        client.connect(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()
        self._client = client

    def _on_connect(self, client, userdata, flags, rc, *args):
        # (Re)connect -> republish presence so a reconnect restores "online".
        if rc != 0:                       # refused CONNACK (VERSION1: int rc, 0 = success)
            LOG.warning("MQTT connect refused (rc=%s)", rc)
            return
        try:
            client.publish(
                self._t_status, json.dumps({"online": True, "ts": int(time.time())}),
                qos=self.QOS_STATUS, retain=True,
            )
        except Exception:
            LOG.exception("status publish failed")

    def publish_spectrum(self, ts, band_id, low_mhz, high_mhz, psd):
        self._publish(
            self._t_spectrum,
            build_spectrum_frame(self.scanner_id, ts, band_id, low_mhz, high_mhz, psd),
            self.QOS_SPECTRUM,
        )

    def publish_detection(self, ts, detections, occupancy):
        self._publish(
            self._t_detection,
            build_detection_payload(self.scanner_id, ts, detections, occupancy),
            self.QOS_DETECTION,
        )

    def _publish(self, topic, payload, qos):
        if self._client is None:
            return
        try:
            self._client.publish(topic, json.dumps(payload), qos=qos, retain=True)
        except Exception:
            LOG.exception("publish to %s failed", topic)

    def close(self, ts):
        if self._client is None:
            return
        try:
            self._client.publish(
                self._t_status, json.dumps({"online": False, "ts": ts}),
                qos=self.QOS_STATUS, retain=True,
            )
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            LOG.exception("close failed")


def build_video_payload(scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64):
    """Pure builder for the fpv/<id>/video contract (analog video frame event)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "center_mhz": center_mhz,
        "standard": standard,
        "line_hz": line_hz,
        "sync_snr_db": sync_snr_db,
        "frame_png_b64": frame_png_b64,
    }


def publish_video_once(host, port, user, password, scanner_id, payload,
                       keepalive=60, client_factory=None, timeout=10.0):
    """One-shot publish of a video frame to fpv/<id>/video (QoS1, retained).

    Deliberately sets NO will/LWT and never writes fpv/<id>/status, so it cannot
    clobber the long-running scan service's retained presence. Returns True on a
    confirmed publish, False (no raise) if the broker is unreachable.
    """
    factory = client_factory or _default_client_factory
    client = factory(f"video-{scanner_id}")
    topic = f"fpv/{scanner_id}/video"
    try:
        if user:
            client.username_pw_set(user, password)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        info = client.publish(topic, json.dumps(payload), qos=1, retain=True)
        info.wait_for_publish(timeout)
        ok = bool(info.is_published())
        client.loop_stop()
        client.disconnect()
        return ok
    except Exception:
        LOG.warning("video publish to %s failed", topic, exc_info=True)
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        return False
