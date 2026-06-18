"""Tests for vision providers.

We don't hit a real Anthropic-Messages-compatible endpoint — instead we
monkeypatch :class:`AsyncAnthropic` so :class:`AnthropicVisionProvider`
builds the right content blocks and parses a fake response.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pytest
from PIL import Image

from screen_mcp.config import Settings, reset_settings_cache
from screen_mcp.vision import get_vision_provider
from screen_mcp.vision.anthropic import AnthropicVisionProvider
from screen_mcp.vision.base import AnalysisResult, VisionProvider
from screen_mcp.frame import make_frame


# ---------------------------------------------------------------------------
# Test image factory
# ---------------------------------------------------------------------------

def _png_bytes() -> bytes:
    img = Image.new("RGB", (40, 30), (100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fake Anthropic response
# ---------------------------------------------------------------------------

@dataclass
class _FakeBlock:
    type: str
    text: str = ""


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 30


@dataclass
class _FakeResponse:
    content: list[_FakeBlock] = field(default_factory=list)
    usage: _FakeUsage = field(default_factory=_FakeUsage)

    def model_dump(self) -> dict:
        return {
            "content": [{"type": b.type, "text": b.text} for b in self.content],
            "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
        }


class _FakeMessages:
    """Stand-in for ``AsyncAnthropic.messages``."""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        self.next_response: _FakeResponse | None = None

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        assert self.next_response is not None, "test forgot to set next_response"
        return self.next_response


class _FakeAsyncAnthropic:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_anthropic() -> tuple[AnthropicVisionProvider, _FakeAsyncAnthropic]:
    client = _FakeAsyncAnthropic(api_key="sk-test-fake")
    provider = AnthropicVisionProvider(api_key="sk-test-fake", model="anthropic-test-model")
    # Swap the real client for our fake.
    provider._client = client  # type: ignore[attr-defined]
    return provider, client


# --- constructor validation ---------------------------------------------------

def test_anthropic_provider_requires_api_key():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicVisionProvider(api_key=None, model="x")


def test_anthropic_provider_requires_model():
    with pytest.raises(ValueError, match="ANTHROPIC_MODEL"):
        AnthropicVisionProvider(api_key="sk-test", model=None)
    with pytest.raises(ValueError, match="ANTHROPIC_MODEL"):
        AnthropicVisionProvider(api_key="sk-test", model="")


def test_anthropic_provider_is_vision_provider_protocol():
    provider = AnthropicVisionProvider(api_key="sk-test", model="x")
    assert isinstance(provider, VisionProvider)
    assert provider.name == "anthropic"


def test_anthropic_provider_records_base_url():
    provider = AnthropicVisionProvider(
        api_key="sk-test", model="x", base_url="https://proxy.example.com/anthropic"
    )
    assert provider.base_url == "https://proxy.example.com/anthropic"


# --- factory ------------------------------------------------------------------

def test_get_vision_provider_returns_anthropic(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    reset_settings_cache()
    settings = Settings(_env_file=None)
    provider = get_vision_provider(settings)
    assert isinstance(provider, AnthropicVisionProvider)
    assert provider.model == settings.anthropic_model


def test_get_vision_provider_passes_base_url(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-domestic-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com/anthropic")
    reset_settings_cache()
    settings = Settings(_env_file=None)
    provider = get_vision_provider(settings)
    assert provider.base_url == "https://proxy.example.com/anthropic"
    assert provider.model == "some-domestic-model"


def test_get_vision_provider_rejects_unknown(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "openai")
    reset_settings_cache()
    settings = Settings(_env_file=None)
    with pytest.raises(NotImplementedError, match="anthropic"):
        get_vision_provider(settings)


def test_anthropic_api_key_falls_back_to_auth_token(monkeypatch):
    """If ANTHROPIC_API_KEY is unset, ANTHROPIC_AUTH_TOKEN (Claude Code's name)
    should be used. Saves users from duplicating the secret in .env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-cp-fake")
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model")
    reset_settings_cache()
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key == "sk-cp-fake"
    provider = get_vision_provider(settings)
    assert provider._client.api_key == "sk-cp-fake"


def test_anthropic_api_key_takes_precedence_over_auth_token(monkeypatch):
    """Explicit ANTHROPIC_API_KEY wins over the fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-explicit")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-cp-fallback")
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model")
    reset_settings_cache()
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key == "sk-explicit"


# --- analyze() behavior -------------------------------------------------------

async def test_analyze_builds_image_content_blocks(fake_anthropic):
    provider, client = fake_anthropic
    frame = make_frame(_png_bytes(), max_edge=200, webp_quality=70)
    client.messages.next_response = _FakeResponse(
        content=[_FakeBlock(type="text", text="hello")]
    )

    await provider.analyze([frame], "what is this?")

    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "anthropic-test-model"
    assert kwargs["max_tokens"] > 0
    msgs = kwargs["messages"]
    assert len(msgs) == 1
    blocks = msgs[0]["content"]
    # 1 image + 1 text
    assert len(blocks) == 2
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["type"] == "base64"
    assert blocks[0]["source"]["media_type"] == "image/webp"
    assert isinstance(blocks[0]["source"]["data"], str) and len(blocks[0]["source"]["data"]) > 0
    assert blocks[1]["type"] == "text"
    assert "what is this?" in blocks[1]["text"]


async def test_analyze_parses_text_and_regions(fake_anthropic):
    provider, client = fake_anthropic
    f1 = make_frame(_png_bytes(), max_edge=200, webp_quality=70)
    f2 = make_frame(_png_bytes(), max_edge=200, webp_quality=70)
    raw_text = (
        'The error is on the submit button.\n'
        '<regions>[{"x":0.5,"y":0.3,"w":0.1,"h":0.05,"label":"submit button"}]</regions>'
    )
    client.messages.next_response = _FakeResponse(content=[_FakeBlock(type="text", text=raw_text)])

    result = await provider.analyze([f1, f2], "where is the button?")
    assert isinstance(result, AnalysisResult)
    assert result.text == "The error is on the submit button."
    assert result.frame_ids == [f1.frame_id, f2.frame_id]
    assert result.model == "anthropic-test-model"
    assert result.tokens_used == 130
    assert len(result.regions) == 1
    assert result.regions[0]["label"] == "submit button"


async def test_analyze_swallows_malformed_regions(fake_anthropic):
    provider, client = fake_anthropic
    f1 = make_frame(_png_bytes(), max_edge=200, webp_quality=70)
    # Valid JSON but not a list — should be ignored, block should still be stripped.
    client.messages.next_response = _FakeResponse(
        content=[_FakeBlock(type="text", text="ok\n<regions>{\"x\":0.5}</regions>")]
    )
    result = await provider.analyze([f1], "anything")
    assert result.text == "ok"
    assert result.regions == []


async def test_analyze_rejects_empty_frames():
    provider = AnthropicVisionProvider(api_key="sk-test", model="x")
    with pytest.raises(ValueError):
        await provider.analyze([], "nothing to see")
