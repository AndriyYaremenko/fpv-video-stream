from device import find_hackrf_sysfs_node


def _mk(node_dir, vendor):
    node_dir.mkdir()
    (node_dir / "idVendor").write_text(vendor + "\n")


def test_find_hackrf_node_returns_matching_device(tmp_path):
    _mk(tmp_path / "1-1.1", "0424")          # SMSC hub
    _mk(tmp_path / "1-1.4", "1d50")          # HackRF
    (tmp_path / "usb1").mkdir()              # non-device entry, no idVendor -> skipped
    node = find_hackrf_sysfs_node(str(tmp_path))
    assert node is not None
    assert node.endswith("1-1.4")


def test_find_hackrf_node_absent(tmp_path):
    _mk(tmp_path / "1-1.1", "0424")
    assert find_hackrf_sysfs_node(str(tmp_path)) is None


def test_find_hackrf_node_missing_root(tmp_path):
    assert find_hackrf_sysfs_node(str(tmp_path / "does-not-exist")) is None
