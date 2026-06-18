"""MCP server entry point. Wires the tools in :mod:`screen_mcp.tools`
into a FastMCP instance and runs it over stdio.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import __version__
from .tools import (
    analyze_screen,
    capture_now,
    list_windows,
    set_polling,
    start_capture,
    stop_capture,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("screen-mcp", instructions=(
    "On-demand screen capture + multi-frame vision analysis. "
    "Call start_capture first, then capture_now or analyze_screen. "
    "Use list_windows to discover window titles on Windows."
))

# Register the six tools. We use the ``name=`` argument so the tool names
# surfaced to the agent are stable regardless of Python identifier changes.
mcp.tool(name="start_capture", description=(
    "Begin a screen-capture session. mode='fullscreen' works on every "
    "platform; mode='window' is Windows-only and requires `target` to be "
    "either a window-title substring or a hex hwnd (e.g. 'Notepad' or '0x1a2b3c')."
))(start_capture)

mcp.tool(name="stop_capture", description="End the current capture session.")(stop_capture)

mcp.tool(name="capture_now", description=(
    "Take a single screenshot on demand. Returns frame metadata (id, size, "
    "phash, timestamp) — NOT the image bytes. Use analyze_screen to actually "
    "read the content."
))(capture_now)

mcp.tool(name="set_polling", description=(
    "Enable or disable a low-frequency background polling loop that keeps "
    "the frame buffer fresh. Default interval is 3 seconds."
))(set_polling)

mcp.tool(name="list_windows", description=(
    "List visible top-level windows. On non-Windows platforms returns []."
))(list_windows)

mcp.tool(name="analyze_screen", description=(
    "Send recent frames to the configured vision provider along with the "
    "user's question, and return the model's answer. lookback_frames "
    "controls how many of the most recent frames to include (default 3)."
))(analyze_screen)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting screen-mcp %s (python %s)", __version__, sys.version.split()[0])
    mcp.run()


if __name__ == "__main__":
    main()
