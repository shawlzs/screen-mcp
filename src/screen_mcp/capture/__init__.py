"""Capture backend abstractions and platform-specific implementations.

Public surface (re-exported here for convenience):

* :class:`Target`, :class:`CaptureBackend` — :mod:`base`
* :class:`MssBackend` — :mod:`mss_backend` (cross-platform fullscreen)
* :class:`WindowsBackend` — :mod:`windows_backend` (win32 single-window)
* :func:`get_default_backend` — factory with platform branching
* :class:`UnsupportedPlatformError`
"""

from __future__ import annotations

import sys

from .base import CaptureBackend, Target, UnsupportedPlatformError
from .mss_backend import MssBackend

__all__ = [
    "CaptureBackend",
    "MssBackend",
    "Target",
    "UnsupportedPlatformError",
    "get_default_backend",
    "list_windows",
]


def get_default_backend(mode: str) -> CaptureBackend:
    """Return the appropriate :class:`CaptureBackend` for ``mode``.

    * ``mode='fullscreen'`` → :class:`MssBackend` (works on every platform).
    * ``mode='window'``     → :class:`WindowsBackend` (win32 only). Raises
      :class:`UnsupportedPlatformError` on Linux/macOS so callers can
      surface a clear error to the agent.
    """
    if mode == "fullscreen":
        return MssBackend()
    if mode == "window":
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                f"window capture is not supported on platform '{sys.platform}'; "
                "use mode='fullscreen' or run on Windows"
            )
        # Local import keeps pywin32 off the import path on non-win32.
        from .windows_backend import WindowsBackend

        return WindowsBackend()
    raise ValueError(f"unknown capture mode: {mode!r}; expected 'fullscreen' or 'window'")


def list_windows() -> list[Target]:
    """Enumerate selectable window targets on this platform.

    On non-Windows platforms this returns ``[]`` rather than raising —
    an agent can call it unconditionally and adapt.
    """
    if sys.platform != "win32":
        return []
    from .windows_backend import WindowsBackend

    return WindowsBackend().list_targets()
