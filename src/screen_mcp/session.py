"""Screen capture session — singleton with a state machine and a polling task.

There is at most one :class:`ScreenCaptureSession` per MCP server process.
Multiple sessions would compete for the same OS capture device (mss / DXGI
session) and produce unreliable results. The :func:`get` factory uses
double-checked locking on an :class:`asyncio.Lock` so callers in the same
event loop get the same instance without races.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

from .capture import CaptureBackend, Target, get_default_backend
from .config import Settings, get_settings
from .frame import Frame, pHashDedupeBuffer

if TYPE_CHECKING:
    from .vision.base import AnalysisResult, VisionProvider

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    STOPPED = "stopped"
    ERROR = "error"


class SessionError(RuntimeError):
    """Raised when an operation is invoked in the wrong session state."""


_SENTINEL = object()


class ScreenCaptureSession:
    """Process-singleton capture session.

    Obtain the instance with :meth:`get`. Tests should call :meth:`reset`
    to clear the singleton between cases.
    """

    _instance: "ScreenCaptureSession | None" = None
    _init_lock: asyncio.Lock | None = None

    # ----- lifecycle ----------------------------------------------------

    def __init__(self) -> None:
        self.state: SessionState = SessionState.IDLE
        self.mode: str | None = None
        self.target: Target | None = None
        self.backend: CaptureBackend | None = None
        self.settings: Settings = get_settings()
        self.buffer: pHashDedupeBuffer = pHashDedupeBuffer(
            maxlen=self.settings.max_frame_buffer,
            threshold=self.settings.phash_dedupe_threshold,
            lookback=self.settings.phash_dedupe_lookback,
        )
        self.polling_task: asyncio.Task | None = None
        self.polling_enabled: bool = False
        self.polling_interval: float = self.settings.default_polling_interval
        self.started_at: float | None = None
        self.error_message: str | None = None
        self.last_frame: Frame | None = None
        # Optional: a pre-supplied capture backend for tests; otherwise the
        # factory is used at start() time.
        self._backend_factory = get_default_backend
        self._vision_provider: "VisionProvider | None" = None

    @classmethod
    def _get_init_lock(cls) -> asyncio.Lock:
        if cls._init_lock is None:
            cls._init_lock = asyncio.Lock()
        return cls._init_lock

    @classmethod
    async def get(cls) -> "ScreenCaptureSession":
        if cls._instance is None:
            async with cls._get_init_lock():
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton — used in tests and on fatal error."""
        cls._instance = None

    # ----- injection hooks (for tests) -----------------------------------

    def set_backend_factory(self, factory: Any) -> None:
        self._backend_factory = factory

    def set_vision_provider(self, provider: "VisionProvider") -> None:
        self._vision_provider = provider

    # ----- state transitions --------------------------------------------

    async def start(self, mode: str, target: Target | None = None) -> None:
        if self.state not in (SessionState.IDLE, SessionState.STOPPED):
            raise SessionError(
                f"cannot start: session is {self.state.value}; "
                "stop the current session first"
            )
        try:
            backend = self._backend_factory(mode)
        except Exception as exc:
            self._enter_error(f"backend init failed: {exc}")
            raise
        self.backend = backend
        self.mode = mode
        self.target = target
        self.buffer.clear()
        self.last_frame = None
        self.error_message = None
        self.started_at = time.time()
        self.state = SessionState.ACTIVE
        logger.info("session started mode=%s target=%s", mode, target)

    async def stop(self) -> None:
        if self.state != SessionState.ACTIVE:
            raise SessionError(f"cannot stop: session is {self.state.value}")
        await self._cancel_polling()
        self.buffer.clear()
        self.last_frame = None
        self.backend = None
        self.state = SessionState.STOPPED
        logger.info("session stopped")

    # ----- capture operations -------------------------------------------

    async def capture_now(self) -> Frame:
        if self.state != SessionState.ACTIVE or self.backend is None:
            raise SessionError("session is not active")
        from .frame import make_frame  # local import to avoid circular at module load

        try:
            raw = self.backend.capture_frame(self.target)
        except Exception as exc:
            self._enter_error(f"capture failed: {exc}")
            raise
        if raw is None:
            raise SessionError("backend returned no frame")
        frame = make_frame(
            raw,
            max_edge=self.settings.capture_max_edge,
            webp_quality=self.settings.webp_quality,
            metadata={"mode": self.mode, "target_id": self.target.id if self.target else None},
        )
        self.buffer.add(frame)
        self.last_frame = frame
        return frame

    async def set_polling(self, enabled: bool, interval_seconds: float | None = None) -> None:
        if interval_seconds is not None and interval_seconds > 0:
            self.polling_interval = float(interval_seconds)
        if enabled:
            if self.state != SessionState.ACTIVE:
                raise SessionError("polling requires an active session")
            self.polling_enabled = True
            if self.polling_task is None or self.polling_task.done():
                self.polling_task = asyncio.create_task(self._polling_loop())
                logger.info("polling started interval=%.2fs", self.polling_interval)
        else:
            self.polling_enabled = False
            await self._cancel_polling()
            logger.info("polling disabled")

    async def _polling_loop(self) -> None:
        try:
            while self.polling_enabled and self.state == SessionState.ACTIVE:
                try:
                    await self.capture_now()
                except SessionError as exc:
                    # Capture failure during polling should not kill the loop;
                    # just log and try again next tick.
                    logger.warning("polling tick failed: %s", exc)
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            logger.debug("polling task cancelled")
            raise

    async def _cancel_polling(self) -> None:
        if self.polling_task is not None and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except (asyncio.CancelledError, Exception):
                pass
        self.polling_task = None

    # ----- vision integration -------------------------------------------

    async def analyze(
        self, query: str, lookback_frames: int = 3
    ) -> "AnalysisResult":
        if self.state != SessionState.ACTIVE:
            raise SessionError("session is not active")
        if self._vision_provider is None:
            from .vision import get_vision_provider

            self._vision_provider = get_vision_provider(self.settings)
        if len(self.buffer) == 0:
            raise SessionError("no frames captured yet; call capture_now first")
        frames = self.buffer.recent(lookback_frames)
        return await self._vision_provider.analyze(frames, query)

    # ----- helpers ------------------------------------------------------

    def recent_frames(self, n: int) -> list[Frame]:
        return self.buffer.recent(n)

    def _enter_error(self, message: str) -> None:
        self.state = SessionState.ERROR
        self.error_message = message
        logger.error("session entered error state: %s", message)
