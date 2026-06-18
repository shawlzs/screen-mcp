"""Unit tests for ScreenCaptureSession.

A ``FakeBackend`` stands in for the real capture backend; a ``FakeVision``
stands in for the real vision provider. The session is reset between tests
to keep the singleton state clean.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from screen_mcp.config import reset_settings_cache
from screen_mcp.frame import Frame
from screen_mcp.session import ScreenCaptureSession, SessionError, SessionState
from screen_mcp.capture.base import Target


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _png_bytes(color: tuple[int, int, int] = (i := 0)) -> bytes:  # noqa: E731
    img = Image.new("RGB", (80, 60), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _struct_payload(kind: int) -> bytes:
    """A payload with STRUCTURALLY distinct content per ``kind`` — phash will
    tell them apart (solid colors would all hash nearly the same)."""
    if kind % 4 == 0:
        img = Image.new("RGB", (80, 60), (255, 255, 255))
    elif kind % 4 == 1:
        img = Image.new("RGB", (80, 60), (0, 0, 0))
        px = img.load()
        for x in range(80):
            for y in range(60):
                if ((x // 10) + (y // 10)) % 2 == 0:
                    px[x, y] = (255, 0, 0)
    elif kind % 4 == 2:
        # Horizontal stripes
        img = Image.new("RGB", (80, 60), (0, 0, 0))
        px = img.load()
        for y in range(60):
            if (y // 8) % 2 == 0:
                for x in range(80):
                    px[x, y] = (0, 255, 0)
    else:
        # Diagonal stripes
        img = Image.new("RGB", (80, 60), (0, 0, 0))
        px = img.load()
        for x in range(80):
            for y in range(60):
                if ((x + y) // 6) % 2 == 0:
                    px[x, y] = (0, 0, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeBackend:
    """A capture backend that returns a stream of pre-baked PNG payloads."""

    name = "fake"
    supports_window = True

    def __init__(self, payloads: list[bytes] | None = None) -> None:
        # Default: a queue of STRUCTURALLY distinct payloads so consecutive
        # captures have different phashes (and aren't deduped).
        self._payloads = list(payloads or [_struct_payload(i) for i in range(6)])
        self.capture_count = 0
        self.fail_on = None  # set to a frame number to raise

    def list_targets(self) -> list[Target]:
        return [Target(id="0x1234", title="fake-window", pid=9999)]

    def capture_frame(self, target: Target | None = None) -> bytes:
        if self.fail_on is not None and self.capture_count == self.fail_on:
            raise RuntimeError("simulated capture failure")
        idx = self.capture_count % len(self._payloads)
        self.capture_count += 1
        return self._payloads[idx]


class FakeVision:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def analyze(self, frames: list[Frame], query: str):
        from screen_mcp.vision.base import AnalysisResult

        self.calls.append(([f.frame_id for f in frames], query))
        return AnalysisResult(
            text=f"fake-answer for: {query}",
            frame_ids=[f.frame_id for f in frames],
            model="fake",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_session():
    """Reset the session singleton and config cache before each test."""
    reset_settings_cache()
    ScreenCaptureSession.reset()
    yield
    ScreenCaptureSession.reset()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
async def started_session() -> tuple[ScreenCaptureSession, FakeBackend]:
    """Boot a session with an injected fake backend. Returns (session, backend)."""
    fake = FakeBackend()
    session = ScreenCaptureSession()  # bypass singleton for clarity
    session.set_backend_factory(lambda m: fake)
    await session.start("window", target=Target(id="0x1234", title="fake-window", pid=9999))
    return session, fake


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

async def test_get_returns_singleton():
    a = await ScreenCaptureSession.get()
    b = await ScreenCaptureSession.get()
    assert a is b


def test_reset_clears_singleton():
    s1 = ScreenCaptureSession()
    s1.state = SessionState.ACTIVE
    ScreenCaptureSession.reset()
    s2 = ScreenCaptureSession()
    assert s1 is not s2
    assert s2.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

async def test_start_from_idle_activates_session(started_session):
    session, _backend = started_session
    assert session.state == SessionState.ACTIVE
    assert session.mode == "window"


async def test_start_while_active_raises(started_session):
    session, _backend = started_session
    with pytest.raises(SessionError):
        await session.start("window")


async def test_stop_then_start_resets_buffer(started_session):
    session, _backend = started_session
    f1 = await session.capture_now()
    f2 = await session.capture_now()
    assert len(session.buffer) == 2
    await session.stop()
    assert session.state == SessionState.STOPPED
    assert len(session.buffer) == 0
    # Start again — buffer should be empty.
    await session.start("window")
    assert len(session.buffer) == 0
    assert session.state == SessionState.ACTIVE


async def test_stop_when_idle_raises():
    session = ScreenCaptureSession()
    with pytest.raises(SessionError):
        await session.stop()


async def test_capture_now_when_idle_raises():
    session = ScreenCaptureSession()
    with pytest.raises(SessionError):
        await session.capture_now()


async def test_backend_factory_failure_enters_error_state():
    session = ScreenCaptureSession()

    def boom(_mode: str):
        raise RuntimeError("backend exploded")

    session.set_backend_factory(boom)
    with pytest.raises(RuntimeError):
        await session.start("window")
    assert session.state == SessionState.ERROR
    assert "backend exploded" in (session.error_message or "")


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

async def test_capture_now_returns_frame_with_metadata(started_session):
    session, _backend = started_session
    frame = await session.capture_now()
    assert isinstance(frame, Frame)
    assert frame.metadata.get("mode") == "window"
    assert frame.metadata.get("target_id") == "0x1234"
    assert session.last_frame is frame


async def test_capture_now_advances_buffer(started_session):
    session, _backend = started_session
    for _ in range(3):
        await session.capture_now()
    assert len(session.buffer) == 3


async def test_capture_now_propagates_backend_errors(started_session):
    session, backend = started_session
    backend.fail_on = 0
    with pytest.raises(RuntimeError):
        await session.capture_now()
    assert session.state == SessionState.ERROR


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

async def test_polling_collects_multiple_frames(started_session):
    session, _backend = started_session
    # Use a tiny interval so the test runs fast.
    await session.set_polling(enabled=True, interval_seconds=0.05)
    # Let it run for ~250ms — should capture at least 2 frames.
    await asyncio.sleep(0.25)
    await session.set_polling(enabled=False)
    assert session.polling_enabled is False
    assert len(session.buffer) >= 2


async def test_polling_disabled_does_not_collect(started_session):
    session, _backend = started_session
    await asyncio.sleep(0.15)
    assert len(session.buffer) == 0


async def test_polling_requires_active_session():
    session = ScreenCaptureSession()
    with pytest.raises(SessionError):
        await session.set_polling(enabled=True, interval_seconds=0.05)


# ---------------------------------------------------------------------------
# Vision integration
# ---------------------------------------------------------------------------

async def test_analyze_requires_active_session():
    session = ScreenCaptureSession()
    with pytest.raises(SessionError):
        await session.analyze("what's on screen?")


async def test_analyze_requires_non_empty_buffer(started_session):
    session, _backend = started_session
    fake = FakeVision()
    session.set_vision_provider(fake)
    with pytest.raises(SessionError):
        await session.analyze("what's on screen?")


async def test_analyze_passes_recent_frames_to_vision(started_session):
    session, _backend = started_session
    fake = FakeVision()
    session.set_vision_provider(fake)
    for _ in range(5):
        await session.capture_now()
    result = await session.analyze("summarize the screen", lookback_frames=3)
    assert result.text == "fake-answer for: summarize the screen"
    assert result.model == "fake"
    assert len(fake.calls) == 1
    frame_ids_passed, query_passed = fake.calls[0]
    assert query_passed == "summarize the screen"
    assert len(frame_ids_passed) == 3
