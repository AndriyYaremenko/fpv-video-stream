import os
import sys
# tx (own modules) + ../video (synth) + ../scan (iq_from_sc16q11 for the round-trip test)
# _HERE is inserted LAST so it lands at sys.path[0]: agent/video happens to also
# have a render.py (unrelated PNG/luma helpers) that would otherwise shadow ours.
_HERE = os.path.dirname(__file__)
for _p in (os.path.join(_HERE, "..", "video"), os.path.join(_HERE, "..", "scan"), _HERE):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
