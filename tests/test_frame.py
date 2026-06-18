"""Unit tests for frame encoding + pHash dedupe buffer."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from screen_mcp.frame import (
    Frame,
    encode_frame,
    hamming_distance,
    make_frame,
    pHashDedupeBuffer,
)


# ---------------------------------------------------------------------------
# Test image factories — produce STRUCTURALLY distinct images so phash
# can actually tell them apart (phash captures structure, not color).
# ---------------------------------------------------------------------------

def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid(color: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> bytes:
    return _png_bytes(Image.new("RGB", size, color))


def _checkerboard(size: tuple[int, int] = (200, 200), tile: int = 20) -> bytes:
    img = Image.new("RGB", size, (0, 0, 0))
    px = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            if ((x // tile) + (y // tile)) % 2 == 0:
                px[x, y] = (255, 255, 255)
    return _png_bytes(img)


def _horizontal_stripes(size: tuple[int, int] = (200, 200), step: int = 10) -> bytes:
    img = Image.new("RGB", size, (0, 0, 0))
    px = img.load()
    for y in range(size[1]):
        if (y // step) % 2 == 0:
            for x in range(size[0]):
                px[x, y] = (200, 200, 200)
    return _png_bytes(img)


def _diagonal_stripes(size: tuple[int, int] = (200, 200), step: int = 8) -> bytes:
    img = Image.new("RGB", size, (0, 0, 0))
    px = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            if ((x + y) // step) % 2 == 0:
                px[x, y] = (180, 80, 80)
    return _png_bytes(img)


def _text_like_image(size: tuple[int, int] = (400, 100)) -> bytes:
    """Fake 'document' image: dark background, light horizontal bars at different x positions."""
    rng = np.random.default_rng(42)
    arr = (rng.random(size) * 60).astype(np.uint8)  # dark noisy bg
    # Add light horizontal "lines of text" at various y
    for y in range(10, size[1], 20):
        line_len = rng.integers(50, size[0])
        start = rng.integers(0, size[0] - line_len)
        arr[y:y + 8, start:start + line_len] = 220
    return _png_bytes(Image.fromarray(arr, mode="L").convert("RGB"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_hamming_distance_zero_for_equal():
    assert hamming_distance(0xDEADBEEF, 0xDEADBEEF) == 0


def test_hamming_distance_max_for_inverse():
    # Inverse of a 64-bit value has all 64 bits flipped.
    assert hamming_distance(0, (1 << 64) - 1) == 64


def test_encode_frame_resizes_oversized_input():
    raw = _solid((255, 0, 0), size=(3000, 1000))
    data, w, h, ph = encode_frame(raw, max_edge=1564, webp_quality=70)
    assert isinstance(data, bytes) and len(data) > 0
    # Long edge was 3000, scaled to 1564
    assert max(w, h) == 1564
    assert 0 <= ph < (1 << 64)


def test_encode_frame_keeps_small_input_intact():
    raw = _solid((10, 20, 30), size=(100, 100))
    data, w, h, _ph = encode_frame(raw, max_edge=1564, webp_quality=80)
    assert (w, h) == (100, 100)
    # WebP magic: 'RIFF' ... 'WEBP'
    assert data[:4] == b"RIFF"
    assert b"WEBP" in data[:12]


def test_make_frame_stamps_id_and_time():
    raw = _solid((128, 128, 128), size=(100, 100))
    f = make_frame(raw, max_edge=800, webp_quality=70)
    assert isinstance(f, Frame)
    assert len(f.frame_id) == 12
    assert f.width == 100 and f.height == 100
    assert f.format == "webp"
    assert f.metadata == {}


def test_dedupe_drops_identical_frames():
    raw = _checkerboard()
    buf = pHashDedupeBuffer(maxlen=10, threshold=6, lookback=3)
    f1 = make_frame(raw, max_edge=400, webp_quality=70)
    f2 = make_frame(raw, max_edge=400, webp_quality=70)  # same image bytes
    assert buf.add(f1) is True
    assert buf.add(f2) is False
    assert len(buf) == 1


def test_dedupe_keeps_structurally_dissimilar_frames():
    buf = pHashDedupeBuffer(maxlen=10, threshold=6, lookback=3)
    assert buf.add(make_frame(_checkerboard(), max_edge=400, webp_quality=70)) is True
    assert buf.add(make_frame(_horizontal_stripes(), max_edge=400, webp_quality=70)) is True
    assert buf.add(make_frame(_diagonal_stripes(), max_edge=400, webp_quality=70)) is True
    assert buf.add(make_frame(_text_like_image(), max_edge=400, webp_quality=70)) is True
    assert len(buf) == 4


def test_dedupe_lookback_bounds_comparison():
    """A new frame should be compared only to the last N entries, not the whole buffer."""
    raw_red = _checkerboard()
    raw_green = _horizontal_stripes()
    raw_blue = _diagonal_stripes()
    # lookback=1: only compare to the single most recent frame.
    buf = pHashDedupeBuffer(maxlen=20, threshold=6, lookback=1)
    assert buf.add(make_frame(raw_red, max_edge=400, webp_quality=70)) is True
    assert buf.add(make_frame(raw_green, max_edge=400, webp_quality=70)) is True
    # With lookback=1, red is only compared to green (which is different), so it's added.
    assert buf.add(make_frame(raw_red, max_edge=400, webp_quality=70)) is True
    assert len(buf) == 3


def test_buffer_respects_maxlen():
    buf = pHashDedupeBuffer(maxlen=4, threshold=6, lookback=3)
    images = [_checkerboard(), _horizontal_stripes(), _diagonal_stripes(), _text_like_image(), _checkerboard()]
    for raw in images:
        buf.add(make_frame(raw, max_edge=200, webp_quality=70))
    # All 5 are structurally distinct (or near-distinct), buffer caps at 4.
    assert len(buf) == 4


def test_recent_returns_last_n():
    f1 = make_frame(_checkerboard(), max_edge=200, webp_quality=70)
    f2 = make_frame(_horizontal_stripes(), max_edge=200, webp_quality=70)
    f3 = make_frame(_diagonal_stripes(), max_edge=200, webp_quality=70)
    buf = pHashDedupeBuffer(maxlen=10, threshold=6, lookback=3)
    buf.add(f1)
    buf.add(f2)
    buf.add(f3)
    assert [f.frame_id for f in buf.recent(2)] == [f2.frame_id, f3.frame_id]
    assert buf.recent(0) == []


def test_clear_empties_buffer():
    raw = _checkerboard()
    buf = pHashDedupeBuffer(maxlen=10, threshold=6, lookback=3)
    buf.add(make_frame(raw, max_edge=200, webp_quality=70))
    assert len(buf) == 1
    buf.clear()
    assert len(buf) == 0
