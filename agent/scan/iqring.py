"""Bounded byte FIFO shared by the in-process view capture sources (HackRF, bladeRF).

Overflow drops the OLDEST buffers — air lost, counted, surfaced as dropped_bytes."""
import threading
import time
from collections import deque


class IqRing:
    """Bounded byte FIFO between a capture producer (USB callback / reader thread)
    and read(). Overflow drops the OLDEST buffers — air lost, counted, surfaced
    as the dropped_chunks stat."""

    def __init__(self, capacity_bytes):
        self._cap = int(capacity_bytes)
        self._d = deque()
        self._size = 0
        self.dropped_bytes = 0
        self._cond = threading.Condition()

    def write(self, buf):
        with self._cond:
            self._d.append(buf)
            self._size += len(buf)
            while self._size > self._cap and len(self._d) > 1:
                old = self._d.popleft()
                self._size -= len(old)
                self.dropped_bytes += len(old)
            self._cond.notify()

    def read(self, n, timeout_s):
        """Exactly n bytes in arrival order, or None on timeout (underrun)."""
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while self._size < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)
            out = bytearray()
            while len(out) < n:
                buf = self._d.popleft()
                take = min(len(buf), n - len(out))
                out += buf[:take]
                if take < len(buf):
                    self._d.appendleft(buf[take:])
                self._size -= take
            return bytes(out)

    def clear(self):
        with self._cond:
            self._d.clear()
            self._size = 0

    def pending(self):
        with self._cond:
            return self._size
