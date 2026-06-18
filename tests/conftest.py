"""Shared test fixtures."""

from __future__ import annotations

import pytest

from screen_mcp import tools as tools_module
from screen_mcp.config import reset_settings_cache
from screen_mcp.session import ScreenCaptureSession
from screen_mcp.capture.base import Target
from ._fakes import FakeBackend, FakeVision


@pytest.fixture(autouse=True)
def _clean_session():
    reset_settings_cache()
    ScreenCaptureSession.reset()
    yield
    ScreenCaptureSession.reset()


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def fake_vision() -> FakeVision:
    return FakeVision()


@pytest.fixture
async def tool_session(fake_backend: FakeBackend, fake_vision: FakeVision, monkeypatch):
    """Patch the tools / session so the MCP tools use fakes end-to-end.

    Returns the ``(session, backend, vision)`` triple. After the test,
    the patches are reverted automatically.
    """
    session = ScreenCaptureSession()
    session.set_backend_factory(lambda m: fake_backend)
    session.set_vision_provider(fake_vision)
    # Force the session singleton to our pre-configured instance.
    ScreenCaptureSession._instance = session  # type: ignore[attr-defined]

    # Patch the warm-up call in tools.start_capture so it doesn't try to
    # instantiate MssBackend (which would fail on a headless host).
    monkeypatch.setattr(tools_module, "get_default_backend", lambda m: fake_backend)

    return session, fake_backend, fake_vision
