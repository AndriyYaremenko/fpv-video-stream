import json
import logging

LOG = logging.getLogger("telemetry.publisher")


def _default_client_factory(client_id):
    import paho.mqtt.client as mqtt
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)


class TelemetryPublisher:
    """Publishes ONLY fpv/<node>/telemetry (retained, QoS1). Sets NO will/LWT and never writes
    fpv/<node>/status — must not clobber the scan agent's retained presence on the shared node id."""

    QOS = 1

    def __init__(self, host, port, user, password, node_id, keepalive=60, client_factory=None):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.node_id, self.keepalive = node_id, keepalive
        self._factory = client_factory or _default_client_factory
        self._topic = f"fpv/{node_id}/telemetry"
        self._client = None

    def connect(self):
        client = self._factory(f"telem-{self.node_id}")
        if self.user:
            client.username_pw_set(self.user, self.password)
        # NOTE: intentionally NO will_set() — telemetry must not affect scan presence.
        client.connect(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()
        self._client = client

    def publish(self, payload):
        if self._client is None:
            return
        try:
            self._client.publish(self._topic, json.dumps(payload), qos=self.QOS, retain=True)
        except Exception:
            LOG.exception("telemetry publish failed")

    def close(self):
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            LOG.exception("close failed")
