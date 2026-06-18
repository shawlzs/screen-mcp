"""Tests for capture backends. The mss fullscreen path can be exercised on
Linux only when a display is available; the Windows path is verified on win32.
"""

from __future__ import annotations

import io
import sys

import pytest
from PIL import Image

from screen_mcp.capture import (
    MssBackend,
    Target,
    UnsupportedPlatformError,
    get_default_backend,
    list_windows,
)
from screen_mcp.capture.base import CaptureBackend


def _mss_or_skip() -> MssBackend:
    """Construct an MssBackend, or skip the test if no display is available."""
    backend = MssBackend()
    try:
        backend.list_targets()
    except Exception as exc:
        pytest.skip(f"mss cannot initialize (likely no display): {exc}")
    return backend


def test_mss_backend_is_capture_backend_protocol():
    backend = MssBackend()
    assert isinstance(backend, CaptureBackend)
    assert backend.name == "mss"
    assert backend.supports_window is False


def test_mss_backend_list_targets_has_fullscreen():
    backend = _mss_or_skip()
    targets = backend.list_targets()
    assert len(targets) >= 1
    full = next((t for t in targets if t.id == "fullscreen"), None)
    assert full is not None
    assert full.bbox is not None
    assert full.bbox[2] > 0 and full.bbox[3] > 0


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only capture test")
def test_windows_backend_is_capture_backend_protocol():
    pytest.importorskip("win32gui")
    from screen_mcp.capture.windows_backend import WindowsBackend

    backend = WindowsBackend()
    assert isinstance(backend, CaptureBackend)
    assert backend.name == "windows"
    assert backend.supports_window is True


def test_get_default_backend_fullscreen_on_linux():
    backend = get_default_backend("fullscreen")
    assert isinstance(backend, MssBackend)


def test_get_default_backend_window_raises_on_linux():
    if sys.platform == "win32":
        pytest.skip("Linux-only test")
    with pytest.raises(UnsupportedPlatformError):
        get_default_backend("window")


def test_get_default_backend_rejects_unknown_mode():
    with pytest.raises(ValueError):
        get_default_backend("screenshot")


def test_list_windows_returns_list_on_linux():
    if sys.platform == "win32":
        pytest.skip("Linux-only assertion")
    assert list_windows() == []


def test_target_is_frozen():
    t = Target(id="x", title="t")
    with pytest.raises(Exception):
        t.id = "y"  # type: ignore[misc]


def test_mss_capture_frame_produces_png_bytes():
    backend = _mss_or_skip()
    raw = backend.capture_frame()
    assert raw is not None
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(raw))
    assert img.size[0] > 0 and img.size[1] > 0
