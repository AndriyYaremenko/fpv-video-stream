import os
import sys

# Put agent/video (own flat modules) and agent/scan (reused config/dweller/publisher)
# on sys.path so flat imports work under pytest. Mirrors agent/scan/conftest.py.
_HERE = os.path.dirname(__file__)
for _p in (_HERE, os.path.join(_HERE, "..", "scan")):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
