import numpy as np
import pytest

from iqio import load_iq


def test_load_iq_reads_interleaved_int8(tmp_path):
    # I=64,Q=-64 repeated -> complex (0.5 - 0.5j) after /128 normalization.
    raw = np.array([64, -64, 64, -64], dtype=np.int8).tobytes()
    f = tmp_path / "cap.iq"
    f.write_bytes(raw)
    iq = load_iq(str(f))
    assert iq.shape == (2,)
    np.testing.assert_allclose(iq.real, [0.5, 0.5], atol=1e-6)
    np.testing.assert_allclose(iq.imag, [-0.5, -0.5], atol=1e-6)


def test_load_iq_rejects_empty_file(tmp_path):
    f = tmp_path / "empty.iq"
    f.write_bytes(b"")
    with pytest.raises(ValueError):
        load_iq(str(f))
