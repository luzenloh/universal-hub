"""
InboundController — координирует входящие платежи на Payfast и Montera.

Жизненный цикл:
  IDLE → POSTING → LIVE → PAYMENT_INCOMING → AWAITING_RECEIPT → COMPLETED
                       ↘ EXPIRED   (если WindowAgent вышел без платежа)
                       ↘ ERROR
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from enum import Enum
from typing import TYPE_CHECKING, Optional

import httpx

from bot.services.payfast_client import PayfastClient
from bot.services.montera_client import MonteraClient
from web.models.schemas import CommandRequest, CommandType, PayoutData

if TYPE_CHECKING:
    from bot.services.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class InboundStatus(str, Enum):
    IDLE = "idle"
    POSTING = "posting"
    LIVE = "live"
    PAYMENT_INCOMING = "payment_incoming"
    AWAITING_RECEIPT = "awaiting_receipt"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ERROR = "error"


_ACTIVE_STATUSES = {InboundStatus.LIVE, InboundStatus.PAYMENT_INCOMING}

_POLL_INTERVAL = 5.0  # seconds


class InboundController:
    def __init__(
        self,
        window_id: str,
        payout: PayoutData,
        secrets: dict,
        orchestrator: "Orchestrator",
    ) -> None:
        self.window_id = window_id
        self.payout = payout
        self.status = InboundStatus.IDLE

        self._payfast = PayfastClient(secrets.get("payfast") or {})
        self._montera = MonteraClient(secrets.get("montera") or {})
        self._orchestrator = orchestrator

        # platform_name → order_id
        self._platform_orders: dict[str, str] = {}
        # platform_name → status string
        self._platform_statuses: dict[str, str] = {}

        self._poll_task: asyncio.Task | None = None
        self._handled = False  # prevent duplicate payment handling

    # ------------------------------------------------------------------ public API

    async def start(self) -> None:
        """Create orders on all platforms and start the poll loop."""
        if self.status != InboundStatus.IDLE:
            return
        self.status = InboundStatus.POSTING
        logger.info("[Inbound %s] Starting — requisite=%s amount=%s",
                    self.window_id, self.payout.recipient, self.payout.amount)

        requisite = self.payout.recipient or ""
        bank = self.payout.bank or ""
        # Parse amount: "50 000 RUB" → 50000.0
        amount = _parse_amount(self.payout.amount)

        await asyncio.gather(
            self._create_payfast_order(requisite, bank, amount),
            self._create_montera_order(requisite, amount),
            return_exceptions=True,
        )

        if not self._platform_orders and not any(
            s in ("live",) for s in self._platform_statuses.values()
        ):
            logger.warning("[Inbound %s] No orders created — staying IDLE", self.window_id)
            self.status = InboundStatus.ERROR
            return

        self.status = InboundStatus.LIVE
        logger.info("[Inbound %s] LIVE — platforms=%s", self.window_id, self._platform_statuses)

        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Cancel all open orders and stop polling."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self.status in (InboundStatus.LIVE, InboundStatus.PAYMENT_INCOMING):
            self.status = InboundStatus.EXPIRED
            await self._cancel_all_orders()

        await self._payfast.close()
        await self._montera.close()
        logger.info("[Inbound %s] Stopped", self.window_id)

    async def handle_expiring(self) -> None:
        """Called by orchestrator when WindowAgent enters EXPIRING state."""
        if self.status != InboundStatus.LIVE:
            return
        logger.info("[Inbound %s] Auto-extending order (EXPIRING detected)", self.window_id)
        try:
            await self._orchestrator.send_command(
                self.window_id,
                CommandRequest(type=CommandType.EXTEND_ORDER, params={}),
            )
        except Exception as exc:
            logger.warning("[Inbound %s] Auto-extend failed: %s", self.window_id, exc)

    # ------------------------------------------------------------------ platform state (for API)

    def get_platform_states(self) -> list[dict]:
        platforms: list[dict] = []
        for name in ("payfast", "montera"):
            status = self._platform_statuses.get(name)
            if status:
                platforms.append({
                    "name": name,
                    "order_id": self._platform_orders.get(name),
                    "status": status,
                })
        return platforms

    # ------------------------------------------------------------------ internal

    async def _create_payfast_order(self, requisite: str, bank: str, amount: float) -> None:
        self._platform_statuses["payfast"] = "posting"
        try:
            order_id = await self._payfast.create_order(requisite, bank, amount)
            self._platform_orders["payfast"] = order_id
            self._platform_statuses["payfast"] = "live"
            logger.info("[Inbound %s] Payfast order created: %s", self.window_id, order_id)
        except NotImplementedError:
            logger.info("[Inbound %s] Payfast create_order not implemented — skipping", self.window_id)
            del self._platform_statuses["payfast"]
        except Exception as exc:
            logger.error("[Inbound %s] Payfast create_order failed: %s", self.window_id, exc)
            self._platform_statuses["payfast"] = "error"

    async def _create_montera_order(self, requisite: str, amount: float) -> None:
        self._platform_statuses["montera"] = "posting"
        try:
            order_id = await self._montera.create_order(requisite, amount)
            self._platform_orders["montera"] = order_id
            self._platform_statuses["montera"] = "live"
            logger.info("[Inbound %s] Montera order created: %s", self.window_id, order_id)
        except Exception as exc:
            logger.error("[Inbound %s] Montera create_order failed: %s", self.window_id, exc)
            self._platform_statuses["montera"] = "error"

    async def _poll_loop(self) -> None:
        logger.info("[Inbound %s] Poll loop started", self.window_id)
        while self.status in _ACTIVE_STATUSES and not self._handled:
            try:
                payfast_orders, montera_orders = await asyncio.gather(
                    self._payfast.get_orders(),
                    self._montera.get_orders(),
                    return_exceptions=True,
                )
                if isinstance(payfast_orders, Exception):
                    payfast_orders = []
                if isinstance(montera_orders, Exception):
                    montera_orders = []

                self._check_payments(
                    payfast_orders=list(payfast_orders),
                    montera_orders=list(montera_orders),
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Inbound %s] Poll error: %s", self.window_id, exc)

            await asyncio.sleep(_POLL_INTERVAL)
        logger.info("[Inbound %s] Poll loop ended (status=%s)", self.window_id, self.status)

    def _check_payments(self, payfast_orders: list[dict], montera_orders: list[dict]) -> None:
        if self._handled:
            return

        # Payfast: status=="PROCESSING" → payment incoming
        pf_order_id = self._platform_orders.get("payfast")
        if pf_order_id:
            for order in payfast_orders:
                if order.get("uuid_system") == pf_order_id or order.get("id") == pf_order_id:
                    if order.get("status") == "PROCESSING":
                        receipt_url = (
                            (order.get("extra_info") or {}).get("check")
                            or (order.get("extra_info") or {}).get("file")
                        )
                        asyncio.create_task(
                            self._handle_payment("payfast", pf_order_id, receipt_url)
                        )
                        return

        # Montera: status=="client_paid" → payment incoming
        mt_order_id = self._platform_orders.get("montera")
        if mt_order_id:
            for order in montera_orders:
                if order.get("uuid") == mt_order_id or order.get("id") == mt_order_id:
                    if order.get("status") == "client_paid":
                        receipt_url = order.get("client_receipt_url")
                        asyncio.create_task(
                            self._handle_payment("montera", mt_order_id, receipt_url)
                        )
                        return

    async def _handle_payment(
        self, platform: str, order_id: str, receipt_url: Optional[str]
    ) -> None:
        if self._handled:
            return
        self._handled = True
        self.status = InboundStatus.PAYMENT_INCOMING
        logger.info("[Inbound %s] Payment detected on %s (order=%s)", self.window_id, platform, order_id)

        # Mark paying platform
        self._platform_statuses[platform] = "payment_incoming"

        # Cancel all OTHER platforms (stubs for now)
        await self._cancel_other_orders(except_platform=platform)

        if not receipt_url:
            logger.warning("[Inbound %s] No receipt URL from %s — skipping upload", self.window_id, platform)
            self.status = InboundStatus.COMPLETED
            return

        self.status = InboundStatus.AWAITING_RECEIPT
        tmp_path: Optional[str] = None
        try:
            tmp_path = await self._download_receipt(receipt_url)
        except Exception as exc:
            logger.error("[Inbound %s] Receipt download failed: %s", self.window_id, exc)
            self.status = InboundStatus.COMPLETED
            return

        try:
            result = await self._orchestrator.send_command(
                self.window_id,
                CommandRequest(type=CommandType.UPLOAD_RECEIPT, params={"path": tmp_path}),
            )
            if result.success:
                logger.info("[Inbound %s] Receipt uploaded to MassMO", self.window_id)
            else:
                logger.warning("[Inbound %s] UPLOAD_RECEIPT failed: %s", self.window_id, result.message)
        except Exception as exc:
            logger.error("[Inbound %s] send_command UPLOAD_RECEIPT error: %s", self.window_id, exc)

        self._platform_statuses[platform] = "completed"
        self.status = InboundStatus.COMPLETED

    async def _download_receipt(self, url: str) -> str:
        """Download receipt to a temp file. Returns file path."""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()

        content_type = r.headers.get("content-type", "")
        if "pdf" in content_type:
            suffix = ".pdf"
        elif "png" in content_type:
            suffix = ".png"
        else:
            suffix = ".jpg"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(r.content)
            return tmp.name

    async def _cancel_all_orders(self) -> None:
        await asyncio.gather(
            self._cancel_payfast_order(),
            self._cancel_montera_order(),
            return_exceptions=True,
        )

    async def _cancel_other_orders(self, except_platform: str) -> None:
        tasks = []
        if except_platform != "payfast" and "payfast" in self._platform_orders:
            tasks.append(self._cancel_payfast_order())
            self._platform_statuses["payfast"] = "cancelled"
        if except_platform != "montera" and "montera" in self._platform_orders:
            tasks.append(self._cancel_montera_order())
            self._platform_statuses["montera"] = "cancelled"
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancel_payfast_order(self) -> None:
        order_id = self._platform_orders.get("payfast")
        if order_id:
            await self._payfast.cancel_order(order_id)

    async def _cancel_montera_order(self) -> None:
        order_id = self._platform_orders.get("montera")
        if order_id:
            await self._montera.cancel_order(order_id)


def _parse_amount(amount_str: Optional[str]) -> float:
    """Parse '50 000 RUB' → 50000.0."""
    if not amount_str:
        return 0.0
    digits = "".join(c for c in amount_str if c.isdigit() or c in ".," )
    digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0
