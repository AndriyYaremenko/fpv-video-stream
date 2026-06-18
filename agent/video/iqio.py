from dweller import iq_from_int8   # reuse the scan service's int8 IQ reader


def load_iq(path):
    """Load a HackRF int8 interleaved (I,Q,I,Q...) capture as a complex array."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw:
        raise ValueError(f"empty IQ file: {path}")
    return iq_from_int8(raw)
