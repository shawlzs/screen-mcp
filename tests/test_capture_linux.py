"""Linux-specific capture tests.

We do NOT assume a display is available — that would need xvfb. Instead we
exercise everything we can about the MssBackend wrapper on a headless host:

* the lazy-init contract (``MssBackend()`` does not touch mss.MSS);
* the wrapper's behavior when mss is mocked to simulate a real display;
* error propagation when mss cannot initialize;
* the full session + frame + buffer pipeline using a mocked mss backend.

For end-to-end capture verification on a real (or virtual) display, run
``xvfb-run -a pytest tests/test_capture_linux.py -v -k real_display`` after
installing ``xvfb``.
"""

from __future__ import annotations

import io
import shutil
import sys
from typing import Any

import pytest
from PIL import Image

from screen_mcp.capture import MssBackend, get_default_backend, list_windows
from screen_mcp.capture.base import UnsupportedPlatformError
from screen_mcp.frame import pHashDedupeBuffer, encode_frame
from screen_mcp.session import ScreenCaptureSession, SessionError, SessionState


def _xvfb_available() -> bool:
    """True if Xvfb is installed — used to skip the real-display integration test."""
    return shutil.which("Xvfb") is not None or shutil.which("xvfb-run") is not None


# ---------------------------------------------------------------------------
# Fake mss client
# ---------------------------------------------------------------------------

class _FakeMSS:
    """Drop-in for ``mss.MSS`` that pretends to be a working display server.

    Returns a single 1920x1080 primary monitor and produces a sequence of
    structurally-distinct PNG payloads on every ``shot()`` call so phash will
    tell successive frames apart.
    """

    def __init__(self, monitors: list[dict] | None = None) -> None:
        self.monitors = monitors or [
            {
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
            },
            {
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
            },
        ]
        self._counter = 0

    def _next_payload(self) -> bytes:
        """Generate a distinct payload each call (counter cycles 0..7)."""
        kind = self._counter % 8
        self._counter += 1
        if kind % 4 == 0:
            img = Image.new("RGB", (640, 360), (255, 255, 255))
        elif kind % 4 == 1:
            img = Image.new("RGB", (640, 360), (0, 0, 0))
            px = img.load()
            for x in range(640):
                for y in range(360):
                    if ((x // 40) + (y // 40)) % 2 == 0:
                        px[x, y] = (255, 0, 0)
        elif kind % 4 == 2:
            img = Image.new("RGB", (640, 360), (0, 0, 0))
            px = img.load()
            for y in range(360):
                if (y // 20) % 2 == 0:
                    for x in range(640):
                        px[x, y] = (0, 255, 0)
        else:
            img = Image.new("RGB", (640, 360), (0, 0, 0))
            px = img.load()
            for x in range(640):
                for y in range(360):
                    if ((x + y) // 12) % 2 == 0:
                        px[x, y] = (0, 0, 255)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def shot(self, output: Any = None, mon: int = 0) -> Any:
        payload = self._next_payload()
        if output is None:
            return payload
        if hasattr(output, "write"):
            output.write(payload)
            return output
        # mss accepts a filename/path-like too; write bytes to it
        with open(output, "wb") as fh:
            fh.write(payload)
        return output


# ---------------------------------------------------------------------------
# Lazy init contract
# ---------------------------------------------------------------------------

def test_mss_backend_construction_does_not_connect_to_display():
    """Constructing MssBackend on a headless host must not raise — mss.MSS
    is only invoked on first capture / list_targets."""
    backend = MssBackend()
    assert backend._sct is None  # noqa: SLF001 — explicit internal check


def test_mss_backend_first_use_initializes_sct(monkeypatch):
    monkeypatch.setattr("mss.MSS", _FakeMSS)
    backend = MssBackend()
    assert backend._sct is None  # noqa: SLF001
    backend.list_targets()  # triggers mss.MSS
    assert backend._sct is not None  # noqa: SLF001


def test_mss_backend_propagates_mss_init_failure(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("cannot open display")

    monkeypatch.setattr("mss.MSS", boom)
    backend = MssBackend()
    with pytest.raises(RuntimeError, match="cannot open display"):
        backend.list_targets()


# ---------------------------------------------------------------------------
# Full pipeline with mocked mss
# ---------------------------------------------------------------------------

def test_mss_backend_list_targets_with_fake_display(monkeypatch):
    monkeypatch.setattr("mss.MSS", _FakeMSS)
    backend = MssBackend()
    targets = backend.list_targets()
    assert len(targets) == 1
    t = targets[0]
    assert t.id == "fullscreen"
    assert t.bbox == (0, 0, 1920, 1080)
    assert "1920" in t.title and "1080" in t.title


def test_mss_backend_capture_returns_png_bytes(monkeypatch):
    monkeypatch.setattr("mss.MSS", _FakeMSS)
    backend = MssBackend()
    raw = backend.capture_frame()
    assert raw is not None and len(raw) > 0
    # PNG magic
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    # And PIL can open it
    img = Image.open(io.BytesIO(raw))
    assert img.size == (640, 360)


def test_mss_backend_full_pipeline_against_real_frame_module(monkeypatch):
    """End-to-end on Linux with a fake display: mss -> PIL -> WebP -> phash."""
    monkeypatch.setattr("mss.MSS", _FakeMSS)
    backend = MssBackend()
    raw = backend.capture_frame()
    data, w, h, ph = encode_frame(raw, max_edge=400, webp_quality=70)
    assert data[:4] == b"RIFF" and b"WEBP" in data[:12]
    assert max(w, h) == 400
    assert 0 <= ph < (1 << 64)


# ---------------------------------------------------------------------------
# Session + mss end-to-end (with mocked display)
# ---------------------------------------------------------------------------

async def test_session_uses_real_mss_code_via_mock(monkeypatch):
    """Plug a fake-but-mss-shaped backend into a real session, drive
    capture_now + buffer + analyze, and verify the whole pipeline works."""
    from tests._fakes import FakeVision

    monkeypatch.setattr("mss.MSS", _FakeMSS)
    backend = MssBackend()  # exercises the real wrapper, not a fake

    session = ScreenCaptureSession()
    session.set_backend_factory(lambda _m: backend)
    vision = FakeVision()
    session.set_vision_provider(vision)

    try:
        await session.start("fullscreen")
        assert session.state == SessionState.ACTIVE
        for _ in range(3):
            await session.capture_now()
        assert len(session.buffer) == 3
        result = await session.analyze("what is on screen?", lookback_frames=2)
        assert result.text.startswith("fake-answer")
        assert len(result.frame_ids) == 2
    finally:
        if session.state == SessionState.ACTIVE:
            await session.stop()


async def test_session_capture_error_enters_error_state(monkeypatch):
    """If the real (mocked) backend raises during capture, the session must
    transition to ERROR — not silently swallow the failure."""
    class _BoomMSS(_FakeMSS):
        def shot(self, output: Any = None, mon: int = 0) -> Any:
            raise RuntimeError("simulated mss failure")

    monkeypatch.setattr("mss.MSS", _BoomMSS)
    backend = MssBackend()
    session = ScreenCaptureSession()
    session.set_backend_factory(lambda _m: backend)
    await session.start("fullscreen")
    with pytest.raises(RuntimeError, match="simulated mss failure"):
        await session.capture_now()
    assert session.state == SessionState.ERROR
    assert "simulated mss failure" in (session.error_message or "")


# ---------------------------------------------------------------------------
# Platform branching
# ---------------------------------------------------------------------------

def test_get_default_backend_window_raises_unsupported_platform_error():
    if sys.platform == "win32":
        pytest.skip("Linux/macOS only")
    with pytest.raises(UnsupportedPlatformError) as exc:
        get_default_backend("window")
    # Error message should mention the platform.
    assert sys.platform in str(exc.value)
    assert "window" in str(exc.value).lower()


def test_list_windows_returns_empty_list_on_linux():
    if sys.platform == "win32":
        pytest.skip("Linux/macOS only")
    assert list_windows() == []


def test_fullscreen_mode_does_not_require_target_lookup(monkeypatch):
    """``start_capture('fullscreen')`` must NOT call ``enumerate_windows`` —
    it should succeed even on a host with no windowing system."""
    import asyncio

    monkeypatch.setattr("mss.MSS", _FakeMSS)
    session = ScreenCaptureSession()
    backend = MssBackend()
    session.set_backend_factory(lambda _m: backend)
    asyncio.run(session.start("fullscreen"))
    assert session.state == SessionState.ACTIVE


# ---------------------------------------------------------------------------
# Optional: real-display integration test (skip when no Xvfb)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform == "win32" or not _xvfb_available(),
    reason="requires Xvfb; install with `apt install xvfb` and re-run with `xvfb-run -a pytest`",
)
def test_real_mss_capture_under_xvfb(monkeypatch):
    """When Xvfb is available (or a real display), the wrapper should be
    able to talk to mss without our mocks."""
    backend = MssBackend()
    targets = backend.list_targets()
    assert len(targets) >= 1
    raw = backend.capture_frame()
    assert raw is not None and raw[:8] == b"\x89PNG\r\n\x1a\n"
