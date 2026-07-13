import os
import sys
# tx (own modules: tx_render, bladerf_tx, ...) + ../video (synth) + ../scan (iq_from_sc16q11 for the round-trip test)
_HERE = os.path.dirname(__file__)
for _p in (_HERE, os.path.join(_HERE, "..", "video"), os.path.join(_HERE, "..", "scan")):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
