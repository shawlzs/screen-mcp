"""Stand-alone test fakes importable from any test module."""

from __future__ import annotations

import io

from PIL import Image

from screen_mcp.capture.base import Target
from screen_mcp.frame import Frame


def struct_payload(kind: int) -> bytes:
    """A structurally distinct PNG payload per ``kind`` (phash will differ)."""
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
        img = Image.new("RGB", (80, 60), (0, 0, 0))
        px = img.load()
        for y in range(60):
            if (y // 8) % 2 == 0:
                for x in range(80):
                    px[x, y] = (0, 255, 0)
    else:
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

    def __init__(self, payloads: list[bytes] | None = None, targets: list[Target] | None = None) -> None:
        self._payloads = list(payloads or [struct_payload(i) for i in range(6)])
        self._targets = targets or [Target(id="0x1234", title="fake-window", pid=9999)]
        self.capture_count = 0
        self.fail_on: int | None = None

    def list_targets(self) -> list[Target]:
        return list(self._targets)

    def capture_frame(self, target: Target | None = None) -> bytes:
        if self.fail_on is not None and self.capture_count == self.fail_on:
            raise RuntimeError("simulated capture failure")
        idx = self.capture_count % len(self._payloads)
        self.capture_count += 1
        return self._payloads[idx]


class FakeVision:
    """A vision provider that records its calls and returns canned answers."""

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
