# dashboard/agent scan — HackRF USB recovery.
#
# On some hosts (notably older Raspberry Pis with the dwc_otg USB2 controller),
# an unclean kill of hackrf_sweep/hackrf_transfer (e.g. a subprocess timeout
# SIGKILL) leaves the HackRF wedged: every subsequent open hangs until the USB
# device is re-enumerated. We recover by toggling the device's sysfs `authorized`
# flag (a software "replug"). Requires root; best-effort and a no-op off Linux.

import logging
import os
import time

LOG = logging.getLogger("scan")

HACKRF_VENDOR = "1d50"  # Great Scott Gadgets (HackRF One)


def find_hackrf_sysfs_node(sysfs_root: str = "/sys/bus/usb/devices") -> str | None:
    """Return the sysfs device directory for the HackRF (idVendor 1d50), or None."""
    try:
        entries = os.listdir(sysfs_root)
    except OSError:
        return None
    for name in entries:
        path = os.path.join(sysfs_root, name)
        try:
            with open(os.path.join(path, "idVendor"), "r") as f:
                if f.read().strip().lower() == HACKRF_VENDOR:
                    return path
        except OSError:
            continue  # interface dirs / non-device entries have no idVendor
    return None


def reset_hackrf(sysfs_root: str = "/sys/bus/usb/devices", settle_s: float = 3.0) -> bool:
    """Best-effort USB re-enumeration of the HackRF via the `authorized` toggle.

    Recovers a device wedged by an unclean kill. Requires root. Returns True if a
    reset was attempted on a found device, False otherwise.
    """
    node = find_hackrf_sysfs_node(sysfs_root)
    if not node:
        LOG.warning("reset_hackrf: no HackRF (vendor %s) under %s", HACKRF_VENDOR, sysfs_root)
        return False
    auth = os.path.join(node, "authorized")
    try:
        with open(auth, "w") as f:
            f.write("0")
        time.sleep(1.0)
        with open(auth, "w") as f:
            f.write("1")
        time.sleep(settle_s)
        LOG.info("reset_hackrf: re-enumerated %s", node)
        return True
    except OSError as e:
        LOG.warning("reset_hackrf: cannot toggle %s: %s", auth, e)
        return False
