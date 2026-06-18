"""Anthropic Messages API vision provider.

This client works with any endpoint that speaks the Anthropic Messages API,
including:
* the official Anthropic API at ``https://api.anthropic.com``;
* Anthropic-compatible domestic-model proxies (e.g. ``https://api.minimaxi.com/anthropic``,
  various domestic LLM gateways that expose ``/v1/messages``).

The model name and base URL are both supplied at construction time; nothing
is hard-coded to a particular vendor.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

from ..frame import Frame
from .base import AnalysisResult

logger = logging.getLogger(__name__)

# Tokens we ask the model to produce; kept modest because the response is
# usually a short Q&A answer + an optional JSON regions block.
_MAX_TOKENS = 1024

# Best-effort extraction of an optional <regions>...</regions> block.
# The block can contain arbitrary JSON-ish text; we try to parse it as a list
# of region objects, and silently drop the whole block from the visible text
# either way.
_REGIONS_RE = re.compile(r"<regions>\s*(.*?)\s*</regions>", re.DOTALL)


def _build_prompt(query: str, n_frames: int) -> str:
    return (
        f"You are looking at {n_frames} screenshot(s) captured from the user's "
        "screen at different moments. Use them to answer the user's question. "
        "If the user asks about something visible on the screen, be specific "
        "(quote text, name UI elements). If the screenshots are not relevant, "
        "say so.\n\n"
        "Reply with a short answer in plain text. If your answer refers to "
        "specific on-screen regions, append a single line:\n"
        '<regions>[{"x":0-1,"y":0-1,"w":0-1,"h":0-1,"label":"..."}]</regions>\n'
        "(coordinates are normalized to 0-1 in image space).\n\n"
        f"User question: {query}"
    )


class AnthropicVisionProvider:
    """Calls an Anthropic-Messages-API-compatible endpoint with WebP image blocks."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from anthropic import AsyncAnthropic

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required (set it in .env or the environment)"
            )
        if not model:
            raise ValueError(
                "ANTHROPIC_MODEL is required (set it in .env or the environment, "
                "e.g. claude-sonnet-4-6 or whatever your proxy expects)"
            )
        # Only pass base_url when set — let the SDK pick its built-in default
        # otherwise. Passing None would still work but the explicit form
        # makes the intent clear in logs and tracebacks.
        self._client = (
            AsyncAnthropic(api_key=api_key, base_url=base_url)
            if base_url
            else AsyncAnthropic(api_key=api_key)
        )
        self.model = model
        self.base_url = base_url

    async def analyze(self, frames: list[Frame], query: str) -> AnalysisResult:
        if not frames:
            raise ValueError("AnthropicVisionProvider.analyze requires at least one frame")

        content: list[dict[str, Any]] = []
        for f in frames:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": f"image/{f.format}",
                        "data": base64.standard_b64encode(f.data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": _build_prompt(query, len(frames))})

        started = time.time()
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
        )
        elapsed = time.time() - started
        logger.info(
            "anthropic vision call: model=%s base_url=%s frames=%d elapsed=%.2fs",
            self.model,
            self.base_url or "<default>",
            len(frames),
            elapsed,
        )

        text, regions = _extract_text_and_regions(response)
        usage = getattr(response, "usage", None)
        tokens_used = (
            (getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0))
            if usage is not None
            else None
        )
        return AnalysisResult(
            text=text,
            frame_ids=[f.frame_id for f in frames],
            regions=regions,
            model=self.model,
            tokens_used=tokens_used,
            raw_provider_response=_safe_dump(response),
        )


def _extract_text_and_regions(response: Any) -> tuple[str, list[dict]]:
    """Pull text out of the response content blocks and a best-effort
    parse of any optional ``<regions>[...]</regions>`` segment.
    """
    parts: list[str] = []
    content = getattr(response, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    full_text = "\n".join(p for p in parts if p).strip()

    regions: list[dict] = []
    m = _REGIONS_RE.search(full_text)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                regions = [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            logger.warning("anthropic vision returned malformed <regions> JSON, ignoring")

    # Always strip the regions block from the user-visible text — the markup
    # is for tooling, not for the human reading the answer.
    text = _REGIONS_RE.sub("", full_text).strip()
    return text, regions


def _safe_dump(response: Any) -> dict | None:
    """Convert the SDK response into a plain dict, best-effort."""
    try:
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "to_dict"):
            return response.to_dict()
    except Exception:
        return None
    return None
