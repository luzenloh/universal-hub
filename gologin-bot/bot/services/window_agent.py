"""
Per-window asyncio.Task + state machine.
Each WindowAgent manages one MassMO profile via pure REST API (no browser).
"""
from __future__ import annotations

import asyncio
import logging
import time
from asyncio import Future, Queue
from typing import Callable, Coroutine, Any

from bot.services.massmo_api import MassmoAuthError, MassmoClient, TokenExpiredError
from web.models.schemas import CommandRequest, CommandResult, CommandType, PayoutData, WindowState, WindowStatus

logger = logging.getLogger(__name__)

_POLL_INTERVALS: dict[WindowStatus, float] = {
    WindowStatus.IDLE: 15.0,
    WindowStatus.SEARCHING: 5.0,
    WindowStatus.ACTIVE_PAYOUT: 3.0,
    WindowStatus.EXPIRING: 3.0,
    WindowStatus.VERIFICATION: 5.0,
    WindowStatus.VERIFICATION_FAILED: 10.0,
    WindowStatus.PAID: 5.0,
    WindowStatus.CONNECTING: 2.0,
    WindowStatus.DISABLED: 30.0,
    WindowStatus.ERROR: 10.0,
    WindowStatus.STOPPED: 60.0,
}

_MAX_BACKOFF = 60.0

_BURST_CMDS = frozenset({
    CommandType.REQUEST_PAYOUT,
    CommandType.CANCEL_PAYOUT,
    CommandType.UPLOAD_RECEIPT,
    CommandType.SELECT_SENDER_BANK,
    CommandType.TOGGLE_SETTING,
    CommandType.EXTEND_ORDER,
})


class WindowAgent:
    def __init__(
        self,
        window_id: str,
        label: str,
        massmo_secret: str,
        on_state_change: Callable[..., Coroutine[Any, Any, None]] | None = None,
        cached_jwt: str | None = None,
    ) -> None:
        self.window_id = window_id
        self.label = label
        self._on_state_change = on_state_change

        self._status = WindowStatus.CONNECTING
        self._payout: PayoutData | None = None
        self._error_msg: str | None = None
        self._last_updated: float = time.time()
        self._min_limit: int | None = None
        self._max_limit: int | None = None

        self._command_queue: Queue[tuple[CommandRequest, Future]] = Queue()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._burst_until: float = 0.0

        self._client = MassmoClient(secret=massmo_secret, label=label, cached_jwt=cached_jwt)

    def get_jwt(self) -> str | None:
        return self._client.get_jwt()

    # ------------------------------------------------------------------ state

    def get_state(self) -> WindowState:
        return WindowState(
            window_id=self.window_id,
            label=self.label,
            status=self._status,
            payout=self._payout,
            error_msg=self._error_msg,
            last_updated=self._last_updated,
            min_limit=self._min_limit,
            max_limit=self._max_limit,
        )

    async def _set_state(
        self,
        status: WindowStatus,
        payout: PayoutData | None = None,
        error_msg: str | None = None,
        min_limit: int | None = None,
        max_limit: int | None = None,
    ) -> None:
        changed = (
            self._status != status
            or self._payout != payout
            or self._error_msg != error_msg
            or self._min_limit != min_limit
            or self._max_limit != max_limit
        )
        self._status = status
        self._payout = payout
        self._error_msg = error_msg
        self._min_limit = min_limit
        self._max_limit = max_limit
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
        await self._client.logout()
        await self._client.close()

    # ------------------------------------------------------------------ main loop

    async def run(self) -> None:
        backoff = 2.0
        ever_connected = False
        while not self._stop_event.is_set():
            try:
                await self._connect()
                ever_connected = True
                backoff = 2.0
                await self._control_loop()
            except asyncio.CancelledError:
                break
            except MassmoAuthError as exc:
                # Auth errors are permanent — don't retry
                logger.error("[%s] Auth error: %s", self.label, exc)
                await self._set_state(WindowStatus.ERROR, error_msg=str(exc))
                break
            except Exception as exc:
                logger.error("[%s] Crashed: %s", self.label, exc)
                await self._set_state(WindowStatus.ERROR, error_msg=str(exc))
                if not ever_connected:
                    await asyncio.sleep(3.0)
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _connect(self) -> None:
        await self._set_state(WindowStatus.CONNECTING)
        logger.info("[%s] Connecting to MassMO API...", self.label)
        await self._client.login()
        logger.info("[%s] Connected", self.label)

    async def _drain_queue(self) -> None:
        while not self._command_queue.empty():
            cmd, future = await self._command_queue.get()
            result = await self._execute_command(cmd)
            if not future.done():
                future.set_result(result)

    async def _control_loop(self) -> None:
        poll_errors = 0
        while not self._stop_event.is_set():
            await self._drain_queue()

            try:
                await asyncio.wait_for(self._poll(), timeout=12.0)
                poll_errors = 0
            except Exception as exc:
                poll_errors += 1
                if poll_errors >= 3:
                    raise
                logger.warning("[%s] Transient poll error %d/3: %s", self.label, poll_errors, exc)

            await self._drain_queue()

            if time.time() < self._burst_until:
                interval = 0.5
            else:
                interval = _POLL_INTERVALS.get(self._status, 5.0)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    _NEEDS_ORDER_CHECK = frozenset({
        WindowStatus.CONNECTING,
        WindowStatus.ACTIVE_PAYOUT,
        WindowStatus.EXPIRING,
        WindowStatus.VERIFICATION,
        WindowStatus.VERIFICATION_FAILED,
        WindowStatus.PAID,
    })

    async def _poll(self) -> None:
        try:
            needs_order = (
                self._status in self._NEEDS_ORDER_CHECK
                or self._client._active_order_id is not None
            )
            if needs_order:
                (status, min_limit, max_limit), (order_status, payout) = await asyncio.gather(
                    self._client.get_state(),
                    self._client.get_active_order(),
                )
                if order_status is not None:
                    status = order_status
            else:
                status, min_limit, max_limit = await self._client.get_state()
                payout = None
            await self._set_state(status, payout=payout, min_limit=min_limit, max_limit=max_limit)

        except TokenExpiredError:
            logger.info("[%s] Token expired, re-logging in...", self.label)
            self._client._jwt = None
            await self._client.login()
            raise
        except Exception as exc:
            logger.warning("[%s] Poll error: %s", self.label, exc)
            raise

    # ------------------------------------------------------------------ commands

    async def enqueue_command(self, cmd: CommandRequest) -> CommandResult:
        if self._status in (WindowStatus.ERROR, WindowStatus.STOPPED):
            if cmd.type != CommandType.REFRESH_STATE:
                return CommandResult(success=False, message=f"Window in {self._status} state")

        loop = asyncio.get_event_loop()
        future: Future[CommandResult] = loop.create_future()
        await self._command_queue.put((cmd, future))
        self._wake_event.set()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=30.0)
        except asyncio.TimeoutError:
            return CommandResult(success=False, message="Command timed out after 30s")

    async def _execute_command(self, cmd: CommandRequest) -> CommandResult:
        logger.info("[%s] Executing command: %s", self.label, cmd.type)
        try:
            if cmd.type == CommandType.REFRESH_STATE:
                await self._poll()
                return CommandResult(success=True)

            elif cmd.type == CommandType.REQUEST_PAYOUT:
                await self._client.start_search()
                await asyncio.sleep(0.3)
                await self._poll()

            elif cmd.type == CommandType.CANCEL_PAYOUT:
                await self._client.cancel_search()
                await asyncio.sleep(0.3)
                await self._poll()

            elif cmd.type == CommandType.SELECT_BANK:
                await self._client.select_bank(cmd.params.get("bank", ""))

            elif cmd.type == CommandType.UPLOAD_RECEIPT:
                await self._client.upload_receipt(cmd.params.get("path", ""))
                await asyncio.sleep(0.3)
                await self._poll()

            elif cmd.type == CommandType.UPDATE_LIMITS:
                await self._client.update_limits(
                    int(cmd.params.get("min", 0)),
                    int(cmd.params.get("max", 0)),
                )

            elif cmd.type == CommandType.SELECT_SENDER_BANK:
                await self._client.set_sender_bank(cmd.params.get("bank_alias", ""))
                await asyncio.sleep(0.3)
                await self._poll()

            elif cmd.type == CommandType.TOGGLE_SETTING:
                await self._client.toggle_setting(
                    cmd.params.get("setting", ""),
                    bool(cmd.params.get("enabled", True)),
                )

            elif cmd.type == CommandType.EXTEND_ORDER:
                await self._client.extend_order()
                await asyncio.sleep(0.5)
                await self._poll()

            if cmd.type in _BURST_CMDS:
                self._burst_until = time.time() + 10.0

            return CommandResult(success=True)
        except Exception as exc:
            logger.error("[%s] Command %s failed: %s", self.label, cmd.type, exc)
            return CommandResult(success=False, message=str(exc))
