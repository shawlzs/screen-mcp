"""Runtime configuration. All values are env-driven; defaults match the plan's MVP settings.

Env vars (case-insensitive, see .env.example):

    VISION_PROVIDER          default "anthropic"
    ANTHROPIC_API_KEY        required (falls back to ANTHROPIC_AUTH_TOKEN)
    ANTHROPIC_MODEL          required (set to whatever your endpoint expects)
    ANTHROPIC_BASE_URL       default None (uses official Anthropic API)
    DEFAULT_POLLING_INTERVAL seconds, default 3.0
    MAX_FRAME_BUFFER         frames, default 20
    PHASH_DEDUPE_THRESHOLD   hamming distance, default 6
    PHASH_DEDUPE_LOOKBACK    frames to compare, default 3
    WEBP_QUALITY             1-100, default 75
    CAPTURE_MAX_EDGE         px, default 1564 (Anthropic recommended max)
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings; loaded once via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Vision provider ---
    vision_provider: str = Field(default="anthropic")
    anthropic_api_key: str | None = Field(default=None)
    anthropic_model: str | None = Field(default=None)
    # When set, points the Anthropic SDK at a compatible proxy
    # (e.g. a domestic-model gateway exposing /v1/messages).
    # Leave as None to use the official Anthropic API.
    anthropic_base_url: str | None = Field(default=None)

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _fallback_to_anthropic_auth_token(cls, v: str | None) -> str | None:
        """If ``ANTHROPIC_API_KEY`` is unset, fall back to ``ANTHROPIC_AUTH_TOKEN``.

        Claude Code (and some proxies) use ``ANTHROPIC_AUTH_TOKEN`` as the
        canonical var; this lets the same shell work for both tools without
        duplicating the secret into ``.env``.
        """
        if v:
            return v
        return os.environ.get("ANTHROPIC_AUTH_TOKEN")

    # --- Sampling ---
    default_polling_interval: float = Field(default=3.0, gt=0.0)
    max_frame_buffer: int = Field(default=20, gt=0)

    # --- pHash dedupe ---
    phash_dedupe_threshold: int = Field(default=6, ge=0, le=64)
    phash_dedupe_lookback: int = Field(default=3, gt=0)

    # --- Image encoding ---
    webp_quality: int = Field(default=75, ge=1, le=100)
    capture_max_edge: int = Field(default=1564, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance (cached)."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings — used in tests to pick up new env values."""
    get_settings.cache_clear()
