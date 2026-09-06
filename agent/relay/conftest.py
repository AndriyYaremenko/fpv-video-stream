import os
import sys
# relay (own flat modules: relayconfig, bladerf_relay, relay_controller) on sys.path for tests
_HERE = os.path.abspath(os.path.dirname(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
