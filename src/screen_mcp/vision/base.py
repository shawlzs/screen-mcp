"""Vision provider protocol and shared result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..frame import Frame


@dataclass
class AnalysisResult:
    """The structured outcome of a vision call.

    ``text`` is the human-readable answer the agent should pass back to the
    user. ``regions`` are optional bounding boxes (normalized 0-1) the model
    referenced. ``raw_provider_response`` is the full provider payload for
    debugging / observability.
    """

    text: str
    frame_ids: list[str] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    model: str = ""
    tokens_used: int | None = None
    raw_provider_response: dict | None = None


@runtime_checkable
class VisionProvider(Protocol):
    """Anything that can turn frames + a query into an :class:`AnalysisResult`."""

    name: str

    async def analyze(self, frames: list["Frame"], query: str) -> AnalysisResult: ...
