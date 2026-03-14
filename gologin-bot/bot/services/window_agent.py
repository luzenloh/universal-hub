"""
Per-window asyncio.Task + state machine.
Each WindowAgent manages a persistent Playwright CDP connection to one GoLogin profile.
"""
from __future__ import annotations

import asyncio
import logging
import time
from asyncio import Future, Queue
from typing import Callable, Coroutine, Any

from playwright.async_api import async_playwright

from bot.services import massmo_actions as actions
from web.models.schemas import CommandRequest, CommandResult, CommandType, PayoutData, WindowState, WindowStatus

logger = logging.getLogger(__name__)

_POLL_INTERVALS: dict[WindowStatus, float] = {
    WindowStatus.IDLE: 5.0,
    WindowStatus.SEARCHING: 5.0,
    WindowStatus.ACTIVE_PAYOUT: 3.0,
    WindowStatus.CONNECTING: 3.0,
    WindowStatus.ERROR: 10.0,
    WindowStatus.STOPPED: 60.0,
}

_MAX_BACKOFF = 60.0


class WindowAgent:
    def __init__(
        self,
        window_id: str,
        label: str,
        ws_url: str,
        on_state_change: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.window_id = window_id
        self.label = label
        self.ws_url = ws_url
        self._on_state_change = on_state_change  # async callback(WindowState)

        self._status = WindowStatus.CONNECTING
        self._payout: PayoutData | None = None
        self._error_msg: str | None = None
        self._last_updated: float = time.time()

        self._command_queue: Queue[tuple[CommandRequest, Future]] = Queue()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        # Playwright objects (kept alive)
        self._pw = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------ state

    def get_state(self) -> WindowState:
        return WindowState(
            window_id=self.window_id,
            label=self.label,
            status=self._status,
            payout=self._payout,
            error_msg=self._error_msg,
            last_updated=self._last_updated,
        )

    async def _set_state(
        self,
        status: WindowStatus,
        payout: PayoutData | None = None,
        error_msg: str | None = None,
    ) -> None:
        changed = (
            self._status != status
            or self._payout != payout
            or self._error_msg != error_msg
        )
        self._status = status
        self._payout = payout
        self._error_msg = error_msg
        self._last_updated = time.time()
        if changed and self._on_state_change:
            try:
                await self._on_state_change(self.get_state())
            except Exception as exc:
                logger.warning("State change callback failed: %s", exc)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name=f"agent-{self.label}")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._set_state(WindowStatus.STOPPED)
        await self._disconnect()

    async def _disconnect(self) -> None:
        """Detach from browser WITHOUT closing it (keeps GoLogin profile alive)."""
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
            self._page = None

    # ------------------------------------------------------------------ main loop

    async def run(self) -> None:
        backoff = 2.0
        while not self._stop_event.is_set():
            try:
                await self._connect()
                backoff = 2.0  # reset on successful connect
                await self._control_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[%s] Crashed: %s", self.label, exc)
                await self._set_state(WindowStatus.ERROR, error_msg=str(exc))
                await self._disconnect()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _connect(self) -> None:
        await self._set_state(WindowStatus.CONNECTING)
        logger.info("[%s] Connecting via CDP: %s", self.label, self.ws_url)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(self.ws_url)
        self._page = await actions.find_or_open_massmo_page(self._browser)
        logger.info("[%s] Connected", self.label)

    async def _control_loop(self) -> None:
        while not self._stop_event.is_set():
            # Drain command queue first
            while not self._command_queue.empty():
                cmd, future = await self._command_queue.get()
                result = await self._execute_command(cmd)
                if not future.done():
                    future.set_result(result)

            # Poll state
            await self._poll()

            interval = _POLL_INTERVALS.get(self._status, 5.0)
            await asyncio.sleep(interval)

    async def _poll(self) -> None:
        try:
            status = await actions.detect_state(self._page)
            payout = None
            if status == WindowStatus.ACTIVE_PAYOUT:
                payout = await actions.extract_payout_data(self._page)
            await self._set_state(status, payout=payout)
        except Exception as exc:
            logger.warning("[%s] Poll error: %s", self.label, exc)
            raise  # trigger reconnect

    # ------------------------------------------------------------------ commands

    async def enqueue_command(self, cmd: CommandRequest) -> CommandResult:
        """Called from orchestrator. Returns result when agent processes it."""
        if self._status == WindowStatus.ERROR or self._status == WindowStatus.STOPPED:
            return CommandResult(success=False, message=f"Window in {self._status} state")

        loop = asyncio.get_event_loop()
        future: Future[CommandResult] = loop.create_future()
        await self._command_queue.put((cmd, future))
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=30.0)
        except asyncio.TimeoutError:
            return CommandResult(success=False, message="Command timed out after 30s")

    async def _execute_command(self, cmd: CommandRequest) -> CommandResult:
        logger.info("[%s] Executing command: %s", self.label, cmd.type)
        try:
            if cmd.type == CommandType.REFRESH_STATE:
                await self._poll()

            elif cmd.type == CommandType.REQUEST_PAYOUT:
                await actions.click_request_payout(self._page)
                await self._poll()

            elif cmd.type == CommandType.CANCEL_PAYOUT:
                await actions.cancel_payout(self._page)
                await self._poll()

            elif cmd.type == CommandType.SELECT_BANK:
                bank = cmd.params.get("bank", "")
                await actions.select_bank(self._page, bank)
                await self._poll()

            elif cmd.type == CommandType.UPLOAD_RECEIPT:
                path = cmd.params.get("path", "")
                await actions.upload_receipt(self._page, path)
                await self._poll()

            elif cmd.type == CommandType.UPDATE_LIMITS:
                new_min = int(cmd.params.get("min", 0))
                new_max = int(cmd.params.get("max", 0))
                await actions.update_limits(self._page, new_min, new_max)

            elif cmd.type == CommandType.TOGGLE_SETTING:
                setting = cmd.params.get("setting", "")
                enabled = bool(cmd.params.get("enabled", True))
                await actions.toggle_setting(self._page, setting, enabled)

            return CommandResult(success=True)
        except Exception as exc:
            logger.error("[%s] Command %s failed: %s", self.label, cmd.type, exc)
            return CommandResult(success=False, message=str(exc))
