"""Capture backend protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class UnsupportedPlatformError(RuntimeError):
    """Raised when a capture mode is not available on the current platform."""


@dataclass(frozen=True)
class Target:
    """Identifies something capture can be aimed at.

    ``id`` is opaque to callers but stable for a given window. For
    :class:`MssBackend` the only valid target is the synthetic
    ``id='fullscreen'`` instance returned by :meth:`list_targets`.
    """

    id: str
    title: str
    pid: int | None = None
    bbox: tuple[int, int, int, int] | None = None  # (x, y, w, h)


@runtime_checkable
class CaptureBackend(Protocol):
    """Abstract screen-capture backend."""

    name: str
    supports_window: bool

    def capture_frame(self, target: Target | None = None) -> bytes | None:
        """Capture a single frame, returning raw image bytes (PNG/BGRA).

        ``None`` means the capture device had nothing to report (rare; usually
        an exception should be raised instead).
        """
        ...

    def list_targets(self) -> list[Target]:
        """Return the list of selectable targets (windows for win32, monitors for mss)."""
        ...
