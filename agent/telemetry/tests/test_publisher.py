from publisher import TelemetryPublisher


class FakeClient:
    def __init__(self):
        self.published = []
        self.will = None
    def username_pw_set(self, u, p): self.creds = (u, p)
    def will_set(self, *a, **k): self.will = (a, k)
    def connect(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def test_publishes_only_telemetry_retained_no_lwt_no_status():
    fake = FakeClient()
    pub = TelemetryPublisher("h", 1883, "u", "p", "bladerf", client_factory=lambda cid: fake)
    pub.connect()
    pub.publish({"node_id": "bladerf", "ts": 1})
    assert fake.will is None                                    # NO LWT
    assert len(fake.published) == 1
    topic, payload, qos, retain = fake.published[0]
    assert topic == "fpv/bladerf/telemetry"
    assert qos == 1 and retain is True
    assert '"node_id": "bladerf"' in payload
    assert all("status" not in t for t, *_ in fake.published)   # NEVER writes status
