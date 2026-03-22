"""Payfast REST client — session auth (email/password + cookie refresh).

Auth flow:
  POST /api/login_trader  → accessToken (1 s TTL) + refreshToken cookie (24 h)
  GET  /api/refresh_trader (with cookie) → new accessToken (1 s TTL)

Because the accessToken expires in 1 second, _refresh() is called before
every API request. The refreshToken cookie (httpOnly, 24 h) is stored in
the httpx.AsyncClient cookie jar automatically.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PayfastClient:
    BASE = "https://payfast.website/api"

    def __init__(self, config: dict) -> None:
        self._email: str = config.get("email", "")
        self._password: str = config.get("password", "")
        self._token: str | None = None
        # follow_redirects=True keeps cookie jar intact across redirects
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._auth_lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    async def _login(self) -> None:
        """POST /login_trader → store accessToken + session cookies."""
        r = await self._client.post(
            f"{self.BASE}/login_trader",
            json={"email": self._email, "password": self._password},
        )
        r.raise_for_status()
        self._token = r.json()["accessToken"]
        logger.debug(
            "Payfast: logged in (idTrader=%s)",
            self._client.cookies.get("idTrader"),
        )

    async def _refresh(self) -> None:
        """GET /refresh_trader → new accessToken (uses refreshToken cookie)."""
        r = await self._client.get(
            f"{self.BASE}/refresh_trader",
            headers={"Authorization": f"Bearer {self._token or ''}"},
        )
        r.raise_for_status()
        self._token = r.json()["accessToken"]

    async def _get_token(self) -> str:
        """Return a fresh accessToken, refreshing/re-logging in as needed.

        Called before every API request because TTL = 1 s.
        """
        async with self._auth_lock:
            if not self._token:
                await self._login()
            try:
                await self._refresh()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    logger.info("Payfast: refresh token expired, re-logging in")
                    await self._login()
                    await self._refresh()
                else:
                    raise
            return self._token  # type: ignore[return-value]

    # ------------------------------------------------------------------ request helpers

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self._get_token()
        r = await self._client.post(
            f"{self.BASE}{path}", json=body, headers=self._headers(token)
        )
        r.raise_for_status()
        return r.json()

    async def _get_req(self, path: str) -> dict[str, Any]:
        token = await self._get_token()
        r = await self._client.get(
            f"{self.BASE}{path}", headers=self._headers(token)
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ public API

    async def get_orders(self, page: int = 1, limit: int = 50) -> list[dict]:
        """Fetch BT payin orders (type=checks).

        Returns list of order dicts. Each dict contains at minimum:
          uuid_system  — order UUID (used for matching/approval)
          status       — "WAIT" | "ACCEPTED" | "PAID" | "SUCCSES" | "ERROR" | "TIMEOUT"
          status_check — int: 0=check processing, 1=check ready, else=no check yet
          amount       — float, payment amount
          extra_info   — dict with "file" key = receipt URL (when status_check==1)
          bill         — dict with "reciver" and "bank"
        """
        try:
            data = await self._post(
                "/get_orders_trader",
                {"type": "checks", "page": page, "limit": limit},
            )
            if data.get("status_pending") == "unauth":
                logger.warning("Payfast get_orders: unauthenticated — will retry after re-login")
                self._token = None
                return []
            return data.get("orders") or []
        except Exception as exc:
            logger.warning("Payfast get_orders failed: %s", exc)
            return []

    async def create_order(self, requisite: str, bank: str, amount: float) -> str:
        """Payfast BT: orders are initiated by clients, not traders.

        Returns a sentinel string "amount:<amount>" so InboundController
        can match incoming orders by amount instead of a pre-created ID.
        """
        logger.info(
            "Payfast create_order: client-initiated flow — watching for amount=%.2f",
            amount,
        )
        return f"amount:{amount}"

    async def cancel_order(self, order_id: str) -> None:
        """No cancel API exists for BT payin orders — no-op."""
        logger.info(
            "Payfast cancel_order: no cancel API for payin BT (order_id=%s)", order_id
        )

    async def confirm_order(self, order_id: str) -> None:
        """Approve a BT payin order: POST /action_orders_payin {action:approve}.

        Called after the receipt has been verified / uploaded to MassMO.
        """
        await self._post("/action_orders_payin", {"action": "approve", "id": order_id})
        logger.info("Payfast: order %s approved", order_id)

    async def get_balance(self) -> dict:
        """GET /get_balance_trader → {balance, balance_hold, ...}."""
        return await self._get_req("/get_balance_trader")

    async def proxy_receipt(self, url: str) -> tuple[bytes, str]:
        """Fetch receipt file from PayFast with Bearer auth.

        Returns (content_bytes, content_type).
        The receipt URLs from extra_info.file require authentication.
        """
        token = await self._get_token()
        r = await self._client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        return r.content, ct

    async def get_requisites(self, page: int = 1, limit: int = 50) -> dict:
        """POST /get_bills → {data: [...], totalPages: N}."""
        try:
            return await self._post("/get_bills", {"page": page, "limit": limit})
        except Exception as exc:
            logger.warning("Payfast get_requisites failed: %s", exc)
            return {"data": [], "totalPages": 0}

    async def create_requisite(self, params: dict) -> dict:
        """POST /create_bill → created requisite dict."""
        return await self._post("/create_bill", params)

    async def archive_requisite(self, req_id: str) -> None:
        """Archive a requisite: POST /action_bill {action:archive}."""
        await self._post("/action_bill", {"action": "archive", "id": req_id})
        logger.info("Payfast: requisite %s archived", req_id)

    async def toggle_requisite(self, req_id: str) -> None:
        """Toggle requisite active/inactive: POST /action_bill {action:toggle}."""
        await self._post("/action_bill", {"action": "toggle", "id": req_id})
        logger.info("Payfast: requisite %s toggled", req_id)

    async def close(self) -> None:
        await self._client.aclose()
