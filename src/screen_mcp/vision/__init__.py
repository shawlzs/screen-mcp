"""Vision provider abstraction and concrete implementations.

The :class:`VisionProvider` Protocol is the only thing the rest of the system
cares about. :func:`get_vision_provider` returns the right implementation
based on :class:`screen_mcp.config.Settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .anthropic import AnthropicVisionProvider
from .base import AnalysisResult, VisionProvider

if TYPE_CHECKING:
    from ..config import Settings

__all__ = ["AnalysisResult", "AnthropicVisionProvider", "VisionProvider", "get_vision_provider"]


def get_vision_provider(settings: "Settings") -> VisionProvider:
    """Instantiate the configured vision provider.

    Currently only ``"anthropic"`` is implemented — it speaks the Anthropic
    Messages API and works with any compatible endpoint (the official API or
    a domestic-model proxy).
    """
    name = settings.vision_provider.lower()
    if name == "anthropic":
        return AnthropicVisionProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
        )
    raise NotImplementedError(
        f"vision provider '{settings.vision_provider}' is not implemented "
        "(supported: 'anthropic')"
    )
