"""Implementation of the 6 MCP tools. Separated from server.py so they can
be unit-tested without spinning up an MCP transport.
"""

from __future__ import annotations

import logging
from typing import Literal

from .capture import get_default_backend, list_windows as enumerate_windows
from .capture.base import Target, UnsupportedPlatformError
from .session import ScreenCaptureSession, SessionError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _session() -> ScreenCaptureSession:
    return await ScreenCaptureSession.get()


def _find_target_by_id_or_title(target: str) -> Target:
    """Resolve a user-supplied target string to a :class:`Target`.

    The string can be either a hex hwnd (with or without the ``0x`` prefix)
    or a window title substring.
    """
    candidates = enumerate_windows()
    if not candidates:
        raise SessionError("no windows are enumerable on this platform")

    normalized = target.strip()
    # Hex hwnd match
    hex_part = normalized[2:] if normalized.lower().startswith("0x") else normalized
    if hex_part and all(c in "0123456789abcdefABCDEF" for c in hex_part):
        wanted = f"0x{int(hex_part, 16):x}"
        for c in candidates:
            if c.id.lower() == wanted.lower():
                return c

    # Substring title match
    matches = [c for c in candidates if normalized.lower() in c.title.lower()]
    if not matches:
        raise SessionError(f"no window matches target={target!r}")
    if len(matches) > 1:
        titles = ", ".join(c.title for c in matches[:5])
        raise SessionError(
            f"target={target!r} matches {len(matches)} windows; please be more "
            f"specific (candidates: {titles})"
        )
    return matches[0]


def _frame_summary(frame) -> dict:
    """Return a JSON-safe summary of a :class:`Frame` (no image bytes)."""
    return {
        "frame_id": frame.frame_id,
        "captured_at": frame.captured_at,
        "width": frame.width,
        "height": frame.height,
        "phash": frame.phash,
        "format": frame.format,
        "metadata": frame.metadata,
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def start_capture(
    mode: Literal["fullscreen", "window"],
    target: str | None = None,
) -> dict:
    """Start a capture session.

    Args:
        mode: ``fullscreen`` (works on every platform) or ``window`` (Windows only).
        target: For ``mode='window'``, a substring of the window title or a
                hex hwnd (with or without ``0x`` prefix). Ignored for
                ``mode='fullscreen'``.

    Returns:
        Session info on success. Raises :class:`SessionError` on invalid state.
    """
    s = await _session()
    if s.state.value not in ("idle", "stopped"):
        raise SessionError(
            f"cannot start: session is already {s.state.value}; "
            "stop the current session first"
        )

    # Platform check first — if mode='window' is requested on Linux/macOS,
    # the UnsupportedPlatformError is the most actionable error and should
    # win over any downstream "no windows" or "no target" complaints.
    try:
        get_default_backend(mode)
    except UnsupportedPlatformError:
        raise

    target_obj = None
    if mode == "window":
        if not target:
            raise SessionError("mode='window' requires a target string (window title or hwnd)")
        target_obj = _find_target_by_id_or_title(target)
    elif mode == "fullscreen" and target is not None:
        logger.info("ignoring target for mode=fullscreen: %s", target)

    await s.start(mode, target_obj)
    return {
        "session_id": "default",
        "mode": mode,
        "target": target_obj.id if target_obj else None,
        "state": s.state.value,
    }


async def stop_capture() -> dict:
    """Stop the active capture session."""
    s = await _session()
    await s.stop()
    return {"stopped": True, "state": s.state.value}


async def capture_now() -> dict:
    """Trigger an on-demand capture and return the new frame's metadata.

    Image bytes are intentionally not returned (would blow up MCP message
    size); use :func:`analyze_screen` to actually look at the content.
    """
    s = await _session()
    frame = await s.capture_now()
    return _frame_summary(frame)


async def set_polling(enabled: bool, interval_seconds: float = 3.0) -> dict:
    """Enable or disable the background polling loop."""
    s = await _session()
    await s.set_polling(enabled=enabled, interval_seconds=interval_seconds)
    return {
        "polling": s.polling_enabled,
        "interval": s.polling_interval,
    }


async def list_windows() -> list[dict]:
    """Enumerate visible top-level windows. Returns ``[]`` on non-Windows."""
    targets = enumerate_windows()
    return [
        {
            "hwnd": int(t.id, 16) if t.id.startswith("0x") else None,
            "id": t.id,
            "title": t.title,
            "pid": t.pid,
            "bbox": t.bbox,
        }
        for t in targets
    ]


async def analyze_screen(query: str, lookback_frames: int = 3) -> dict:
    """Send recent frames to the vision provider with the user's query.

    Args:
        query: The user's question about the screen.
        lookback_frames: How many recent frames to include (1-N).
    """
    if not query or not query.strip():
        raise SessionError("query must be a non-empty string")
    if lookback_frames < 1:
        raise SessionError("lookback_frames must be >= 1")

    s = await _session()
    result = await s.analyze(query=query, lookback_frames=lookback_frames)
    return {
        "text": result.text,
        "frame_ids": result.frame_ids,
        "region_count": len(result.regions),
        "regions": result.regions,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
