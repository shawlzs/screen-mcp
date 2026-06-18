"""Cross-platform fullscreen capture via the ``mss`` library.

mss is fast and works on Linux/Windows/macOS. It does NOT support capturing
a single application window — for that, see :mod:`windows_backend` on win32.

The mss instance is constructed lazily on first use so that simply importing
this module — or instantiating :class:`MssBackend` — never fails on a
headless host. The actual X/D3D connection is opened at the first call to
:meth:`capture_frame` or :meth:`list_targets`.
"""

from __future__ import annotations

import threading
from typing import Any

from .base import Target


class MssBackend:
    """Fullscreen capture using mss. ``target`` is ignored — always captures
    the primary monitor.
    """

    name = "mss"
    supports_window = False

    def __init__(self) -> None:
        self._sct: Any | None = None
        self._lock = threading.Lock()

    def _ensure_sct(self) -> Any:
        if self._sct is None:
            with self._lock:
                if self._sct is None:
                    import mss as _mss  # local import so headless hosts don't fail at import time

                    self._sct = _mss.MSS()
        return self._sct

    def list_targets(self) -> list[Target]:
        sct = self._ensure_sct()
        # mss.monitors[0] is the "all monitors" virtual monitor; [1] is primary.
        monitors = list(sct.monitors[1:])
        if not monitors:
            return []
        primary = monitors[0]
        return [
            Target(
                id="fullscreen",
                title=f"Primary monitor {primary['width']}x{primary['height']}",
                pid=None,
                bbox=(primary["left"], primary["top"], primary["width"], primary["height"]),
            )
        ]

    def capture_frame(self, target: Target | None = None) -> bytes | None:
        sct = self._ensure_sct()
        # mss.shot() returns the filename of the saved PNG. We then read it
        # into memory and delete the temp file.
        filename = sct.shot(mon=1)
        try:
            with open(filename, "rb") as f:
                return f.read()
        finally:
            import os
            try:
                os.remove(filename)
            except OSError:
                pass
