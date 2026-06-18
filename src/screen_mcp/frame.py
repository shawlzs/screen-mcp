"""Frame model and pHash-based sliding-window dedupe buffer.

A frame is the result of one capture: a WebP-encoded image plus its perceptual
hash. Backends produce raw bytes (PNG/BGRA) — :func:`encode_frame` is the
single funnel that resizes, WebP-encodes, and computes the phash before a
frame enters the buffer.
"""

from __future__ import annotations

import io
import time
from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4

import imagehash
import numpy as np
from PIL import Image


@dataclass
class Frame:
    """One captured screenshot, WebP-encoded, with phash for dedupe."""

    frame_id: str
    data: bytes
    width: int
    height: int
    captured_at: float
    phash: int
    format: str = "webp"
    # Optional metadata — backends can attach (e.g., target hwnd, monitor id).
    metadata: dict = field(default_factory=dict)


def encode_frame(
    raw_bytes: bytes,
    max_edge: int,
    webp_quality: int,
) -> tuple[bytes, int, int, int]:
    """Resize, WebP-encode, and phash a raw image payload.

    Returns ``(webp_bytes, width, height, phash)``.
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        w, h = img.size

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=webp_quality, method=4)
    ph = imagehash.phash(img)
    # ImageHash is an 8x8 bool array for default phash — pack into a 64-bit int.
    bits = np.packbits(np.asarray(ph.hash).flatten().astype(np.uint8))
    phash_int = int.from_bytes(bits.tobytes(), "big")
    return out.getvalue(), w, h, phash_int


def make_frame(
    raw_bytes: bytes,
    max_edge: int = 1564,
    webp_quality: int = 75,
    metadata: dict | None = None,
) -> Frame:
    """Build a :class:`Frame` from raw capture bytes, stamping it with id+time."""
    data, w, h, ph = encode_frame(raw_bytes, max_edge, webp_quality)
    return Frame(
        frame_id=uuid4().hex[:12],
        data=data,
        width=w,
        height=h,
        captured_at=time.time(),
        phash=ph,
        metadata=metadata or {},
    )


def hamming_distance(a: int, b: int) -> int:
    """Number of differing bits between two 64-bit phash values."""
    return bin(a ^ b).count("1")


class pHashDedupeBuffer:
    """Sliding window of frames with perceptual-hash near-duplicate suppression.

    New frames are compared against the most recent ``lookback`` entries. If
    the hamming distance to any of them is below ``threshold``, the new frame
    is dropped (no need to store a near-identical capture).
    """

    def __init__(self, maxlen: int = 20, threshold: int = 6, lookback: int = 3) -> None:
        self.maxlen = maxlen
        self.threshold = threshold
        self.lookback = lookback
        self._buf: deque[Frame] = deque(maxlen=maxlen)

    def add(self, frame: Frame) -> bool:
        """Add ``frame`` unless it is a near-duplicate of a recent frame."""
        for prev in list(self._buf)[-self.lookback :]:
            if hamming_distance(prev.phash, frame.phash) < self.threshold:
                return False
        self._buf.append(frame)
        return True

    def recent(self, n: int) -> list[Frame]:
        """Return the last ``n`` frames (oldest-first)."""
        if n <= 0:
            return []
        return list(self._buf)[-n:]

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)
