"""Windows-only single-window capture via Win32 PrintWindow.

This module is **not** importable on non-win32 platforms — the import
guard at module level makes the constraint explicit. Callers should use
:func:`screen_mcp.capture.get_default_backend` to receive a backend
appropriate for the current platform.
"""

from __future__ import annotations

import ctypes
import io
import sys

if sys.platform != "win32":
    raise ImportError(
        "windows_backend can only be imported on Windows "
        f"(current platform: {sys.platform})"
    )

import win32gui  # type: ignore[import-not-found]  # noqa: E402
import win32ui  # type: ignore[import-not-found]  # noqa: E402
from PIL import Image  # noqa: E402

from .base import Target  # noqa: E402

# PrintWindow flag — forces the DWM to redraw the window's contents (including
# composited surfaces) into the supplied HDC. Without this, hardware-accelerated
# windows return blank bitmaps.
PW_RENDERFULLCONTENT = 0x00000002


def _parse_hwnd(target_id: str) -> int:
    """Parse a target ``id`` (hex hwnd string) back to an int."""
    if not target_id.startswith("0x"):
        raise ValueError(f"unexpected window target id format: {target_id!r}")
    return int(target_id, 16)


def _format_hwnd(hwnd: int) -> str:
    return f"0x{hwnd:x}"


def _enum_visible_windows() -> list[Target]:
    """Enumerate top-level visible windows, filtering out owner/empty-titled ones."""

    results: list[Target] = []

    def callback(hwnd: int, _ctx: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        # Skip tool windows that are clearly not user-selectable
        # (e.g., the Program Manager class on some Windows builds).
        cls = win32gui.GetClassName(hwnd)
        if cls in {"Progman", "Button"}:
            return
        try:
            rect = win32gui.GetWindowRect(hwnd)
            _thread, pid = win32gui.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        x, y, right, bottom = rect
        w, h = right - x, bottom - y
        if w <= 0 or h <= 0:
            return
        results.append(
            Target(
                id=_format_hwnd(hwnd),
                title=title,
                pid=pid,
                bbox=(x, y, w, h),
            )
        )

    win32gui.EnumWindows(callback, None)
    return results


class WindowsBackend:
    """Single-window capture using Win32 ``PrintWindow`` with the
    ``PW_RENDERFULLCONTENT`` flag, so DWM-composed surfaces come through.
    """

    name = "windows"
    supports_window = True

    def list_targets(self) -> list[Target]:
        return _enum_visible_windows()

    def capture_frame(self, target: Target | None = None) -> bytes | None:
        if target is None or target.id == "fullscreen":
            raise ValueError("WindowsBackend requires a window target (id != 'fullscreen')")
        hwnd = _parse_hwnd(target.id)
        return _print_window(hwnd)


def _print_window(hwnd: int) -> bytes:
    """Capture a single HWND into PNG bytes via PrintWindow."""
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"window {hwnd} has non-positive size {w}x{h}")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)

        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if result == 0:
            raise RuntimeError(f"PrintWindow failed for hwnd 0x{hwnd:x}")

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            save_dc.DeleteDC()
        except Exception:
            pass
        try:
            mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass
