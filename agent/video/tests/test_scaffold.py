def test_can_import_reused_scan_modules():
    # The conftest path shim must expose agent/scan's flat modules to agent/video.
    from dweller import iq_from_int8   # reused IQ reader
    import publisher                   # reused MqttPublisher home
    assert callable(iq_from_int8)
    assert hasattr(publisher, "MqttPublisher")
