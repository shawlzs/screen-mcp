"""Tool-level tests. We exercise the tool functions directly (not over MCP)
to verify return shapes and error behavior.
"""

from __future__ import annotations

import sys

import pytest

from screen_mcp.session import ScreenCaptureSession, SessionError
from screen_mcp.tools import (
    analyze_screen,
    capture_now,
    list_windows,
    set_polling,
    start_capture,
    stop_capture,
)
from ._fakes import FakeVision


# ---------------------------------------------------------------------------
# start_capture
# ---------------------------------------------------------------------------

async def test_start_capture_fullscreen(tool_session):
    result = await start_capture("fullscreen")
    assert result["mode"] == "fullscreen"
    assert result["state"] == "active"
    assert result["target"] is None


async def test_start_capture_rejects_already_active(tool_session):
    await start_capture("fullscreen")
    with pytest.raises(SessionError):
        await start_capture("fullscreen")


async def test_start_capture_window_requires_target(tool_session):
    # On Linux list_windows returns []; the "target required" check should
    # not depend on platform availability.
    with pytest.raises(SessionError):
        await start_capture("window", target=None)


async def test_start_capture_window_unsupported_on_linux():
    """End-to-end test of the platform-branching error path — does NOT use
    the conftest monkeypatch, so it exercises the real ``get_default_backend``
    call in :func:`start_capture` and verifies the unsupported_platform error.
    """
    if sys.platform == "win32":
        pytest.skip("Linux/macOS only")
    from screen_mcp.capture.base import UnsupportedPlatformError

    with pytest.raises(UnsupportedPlatformError):
        await start_capture("window", target="Notepad")


# ---------------------------------------------------------------------------
# stop_capture
# ---------------------------------------------------------------------------

async def test_stop_capture_after_start(tool_session):
    await start_capture("fullscreen")
    result = await stop_capture()
    assert result["stopped"] is True
    assert result["state"] == "stopped"


async def test_stop_capture_when_idle_raises():
    with pytest.raises(SessionError):
        await stop_capture()


# ---------------------------------------------------------------------------
# capture_now
# ---------------------------------------------------------------------------

async def test_capture_now_returns_metadata(tool_session):
    await start_capture("fullscreen")
    result = await capture_now()
    assert "frame_id" in result
    assert result["width"] > 0 and result["height"] > 0
    assert result["format"] == "webp"
    assert isinstance(result["captured_at"], float)


async def test_capture_now_requires_active_session():
    with pytest.raises(SessionError):
        await capture_now()


# ---------------------------------------------------------------------------
# set_polling
# ---------------------------------------------------------------------------

async def test_set_polling_enables_loop(tool_session):
    await start_capture("fullscreen")
    result = await set_polling(enabled=True, interval_seconds=3.0)
    assert result["polling"] is True
    assert result["interval"] == 3.0
    await set_polling(enabled=False)


async def test_set_polling_disables_loop(tool_session):
    await start_capture("fullscreen")
    await set_polling(enabled=True, interval_seconds=0.5)
    result = await set_polling(enabled=False)
    assert result["polling"] is False


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------

async def test_list_windows_returns_list():
    result = await list_windows()
    assert isinstance(result, list)
    if sys.platform != "win32":
        assert result == []


# ---------------------------------------------------------------------------
# analyze_screen
# ---------------------------------------------------------------------------

async def test_analyze_screen_happy_path(tool_session):
    _session, _backend, vision = tool_session
    await start_capture("fullscreen")
    for _ in range(3):
        await capture_now()

    result = await analyze_screen("what's on screen?", lookback_frames=2)
    assert result["text"] == "fake-answer for: what's on screen?"
    assert result["model"] == "fake"
    assert result["region_count"] == 0
    assert len(result["frame_ids"]) == 2
    assert len(vision.calls) == 1


async def test_analyze_screen_rejects_empty_query(tool_session):
    await start_capture("fullscreen")
    await capture_now()
    with pytest.raises(SessionError):
        await analyze_screen("", lookback_frames=1)


async def test_analyze_screen_rejects_lookback_below_one(tool_session):
    await start_capture("fullscreen")
    await capture_now()
    with pytest.raises(SessionError):
        await analyze_screen("anything", lookback_frames=0)


async def test_analyze_screen_requires_active_session():
    with pytest.raises(SessionError):
        await analyze_screen("anything")


async def test_analyze_screen_requires_buffer_not_empty(tool_session):
    await start_capture("fullscreen")
    with pytest.raises(SessionError):
        await analyze_screen("anything")
