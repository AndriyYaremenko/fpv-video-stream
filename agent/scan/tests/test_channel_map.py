from channel_map import nearest_channel


def test_maps_to_nearest_5g8_channel():
    assert nearest_channel(5801.0) == "F4"      # F4 = 5800


def test_returns_none_when_far():
    assert nearest_channel(5500.0) is None


def test_maps_12g_channel():
    assert nearest_channel(1161.0) == "L3"      # L3 = 1160
