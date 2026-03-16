"""
Pure httpx MassMO API client — no browser or Playwright required.

Auth:   POST /api/massmo/v1/users/tokens  {"secret": "..."}  → JWT (1 year)
Logout: DELETE /api/massmo/v1/users/tokens

All other endpoints require Authorization: Bearer {jwt}
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from web.models.schemas import PayoutData, WindowStatus

logger = logging.getLogger(__name__)

_API_BASE = "https://findssnet.io/api/massmo/v1"
_DDOS_GUARD_URL = "https://findssnet.io/"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Origin": "https://massmo.io",
    "Referer": "https://massmo.io/",
    "Accept": "application/json, text/plain, */*",
    "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


class MassmoAuthError(Exception):
    """Raised when JWT cannot be obtained (wrong secret, session limit, etc.)."""


class MassmoAPIError(Exception):
    """Raised on unexpected API errors."""


class TokenExpiredError(MassmoAPIError):
    """Raised when the JWT has expired (HTTP 401 from MassMO)."""


class MassmoClient:
    """
    Stateful httpx client for one MassMO profile.

    Usage:
        client = MassmoClient(secret="abc123", label="M1")
        await client.login()
        status, min_l, max_l = await client.get_state()
        await client.close()
    """

    def __init__(self, secret: str, label: str, cached_jwt: str | None = None) -> None:
        self.secret = secret
        self.label = label
        self._jwt: str | None = cached_jwt
        self._active_order_id: int | None = None
        self._sender_bank_name: str | None = None
        self._client = httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
            http2=False,
        )
        self._cookies_ready = False

    # ------------------------------------------------------------------ auth

    async def _ensure_ddos_cookies(self) -> None:
        """Prime DDoS Guard cookie jar with a warm-up GET."""
        if self._cookies_ready:
            return
        try:
            await self._client.get(_DDOS_GUARD_URL)
            self._cookies_ready = True
        except Exception as exc:
            logger.warning("[%s] DDoS cookie warm-up failed: %s", self.label, exc)

    async def login(self) -> None:
        """Authenticate with MassMO API. Skips if JWT already cached."""
        if self._jwt:
            logger.info("[%s] Using cached JWT", self.label)
            return
        await self._ensure_ddos_cookies()
        r = await self._client.post(
            f"{_API_BASE}/users/tokens",
            json={"secret": self.secret},
            headers={"Content-Type": "application/json"},
        )
        body = r.json()

        if r.status_code == 200:
            self._jwt = body["access_token"]
            logger.info("[%s] MassMO login OK (jwt_len=%d)", self.label, len(self._jwt))
            return

        code = body.get("result_code", "")
        msg = body.get("message", "")

        if code == "max_access_token_count":
            raise MassmoAuthError(
                f"[{self.label}] Session limit reached. "
                "Log out from other MassMO sessions (GoLogin browsers) and retry."
            )
        raise MassmoAuthError(f"[{self.label}] Login failed {r.status_code}: {msg}")

    def get_jwt(self) -> str | None:
        return self._jwt

    async def logout(self) -> None:
        """Revoke the current JWT (best-effort)."""
        if not self._jwt:
            return
        try:
            await self._client.request(
                "DELETE",
                f"{_API_BASE}/users/tokens",
                headers={"Authorization": f"Bearer {self._jwt}",
                         "Content-Type": "application/json"},
            )
            logger.info("[%s] MassMO logout OK", self.label)
        except Exception as exc:
            logger.debug("[%s] Logout error (non-critical): %s", self.label, exc)
        finally:
            self._jwt = None

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ internal helpers

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._jwt}", "Content-Type": "application/json"}

    async def _get(self, path: str) -> httpx.Response:
        r = await self._client.get(f"{_API_BASE}/{path}", headers=self._auth_headers())
        if r.status_code == 401:
            raise TokenExpiredError()
        return r

    async def _post(self, path: str, body: dict | None = None) -> httpx.Response:
        r = await self._client.post(
            f"{_API_BASE}/{path}",
            json=body or {},
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        return r

    async def _delete(self, path: str) -> httpx.Response:
        r = await self._client.request(
            "DELETE",
            f"{_API_BASE}/{path}",
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        return r

    # ------------------------------------------------------------------ state polling

    async def get_state(self) -> tuple[WindowStatus, int | None, int | None]:
        """GET /executor → (status, min_limit, max_limit)"""
        r = await self._get("executor")
        data: dict = (r.json().get("data") or {})

        executor_state = data.get("state", "")
        payout_state = (data.get("payout_state") or "").lower()
        min_limit = data.get("min_amount")
        max_limit = data.get("max_amount")

        if executor_state == "disabled":
            return WindowStatus.DISABLED, min_limit, max_limit
        if payout_state == "in_line":
            return WindowStatus.SEARCHING, min_limit, max_limit
        if payout_state == "idle" or not payout_state:
            return WindowStatus.IDLE, min_limit, max_limit

        logger.warning("[%s] Unknown payout_state=%r (state=%r) → IDLE",
                       self.label, payout_state, executor_state)
        return WindowStatus.IDLE, min_limit, max_limit

    async def get_active_order(self) -> tuple[WindowStatus | None, PayoutData | None]:
        """GET /payout_orders/active → (ACTIVE_PAYOUT/PAID, PayoutData) or (None, None)"""
        r = await self._get("payout_orders/active")

        if r.status_code == 404:
            self._active_order_id = None
            return None, None

        body = r.json()
        if body.get("status") == "not_found" or not body.get("data"):
            self._active_order_id = None
            return None, None

        data: dict = body["data"]
        logger.debug("[%s] Active order: %s", self.label, data)

        self._active_order_id = data.get("id")

        order_status_raw = (data.get("status") or data.get("state") or "").lower()
        if "paid" in order_status_raw or "success" in order_status_raw:
            win_status = WindowStatus.PAID
        elif order_status_raw == "verification":
            win_status = WindowStatus.VERIFICATION
        elif order_status_raw == "verification_failed":
            win_status = WindowStatus.VERIFICATION_FAILED
        else:
            win_status = WindowStatus.ACTIVE_PAYOUT

        def _first(*keys: str) -> str | None:
            for k in keys:
                v = data.get(k)
                if v is not None:
                    return str(v)
            return None

        sender_bank = _first("sender_bank_name") or self._sender_bank_name
        return win_status, PayoutData(
            amount=_first("amount", "payout_amount", "sum"),
            bank=_first("bank_name", "bank", "bank_title"),
            recipient=_first("card_number", "phone", "account_number", "requisite"),
            timer=_first("expired_at", "expires_at", "deadline"),
            rate=_first("rate", "exchange_rate", "course"),
            sender_bank=sender_bank,
        )

    # ------------------------------------------------------------------ payout actions

    async def start_search(self) -> None:
        """Start searching. Real endpoint: GET /executor/enqueue"""
        r = await self._get("executor/enqueue")
        if r.status_code not in (200, 201, 204):
            body = r.json()
            errors = body.get("errors") or {}
            payouter_err = errors.get("payouter", [])
            if payouter_err:
                raise RuntimeError(f"Payouter: {', '.join(payouter_err)}")
            logger.warning("[%s] start_search: unexpected %d — body: %s",
                           self.label, r.status_code, r.text[:300])

    async def set_sender_bank(self, bank_alias: str) -> None:
        """Set sender bank for the active order. PATCH /payout_orders/{id}"""
        if self._active_order_id is None:
            raise RuntimeError(f"[{self.label}] No active order to set sender bank for")
        r = await self._client.patch(
            f"{_API_BASE}/payout_orders/{self._active_order_id}",
            json={"sender_bank_name": bank_alias},
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        if r.status_code not in (200, 201, 204):
            logger.warning("[%s] set_sender_bank: %d — body: %s",
                           self.label, r.status_code, r.text[:300])
        else:
            self._sender_bank_name = bank_alias
            logger.info("[%s] Sender bank set to %s", self.label, bank_alias)

    async def cancel_search(self) -> None:
        """Cancel search or active payout.
        SEARCHING → GET /executor/dequeue
        ACTIVE_PAYOUT → POST /payout_orders/{id}/cancel
        """
        if self._active_order_id:
            r = await self._post(f"payout_orders/{self._active_order_id}/cancel")
            if r.status_code not in (200, 201, 204):
                logger.warning("[%s] cancel_order: unexpected %d — body: %s",
                               self.label, r.status_code, r.text[:300])
            else:
                self._active_order_id = None
        else:
            r = await self._get("executor/dequeue")
            if r.status_code not in (200, 201, 204):
                logger.warning("[%s] dequeue: unexpected %d — body: %s",
                               self.label, r.status_code, r.text[:300])

    async def upload_receipt(self, file_path: str) -> None:
        """Upload receipt. Endpoint: POST /payout_orders/{id}/verification, field: proofs[]"""
        order_id = self._active_order_id
        if order_id is None:
            raise RuntimeError(f"[{self.label}] No active order to upload receipt for")

        url = f"{_API_BASE}/payout_orders/{order_id}/verification"
        mime = "image/jpeg"
        fname = file_path.split("/")[-1]
        if fname.lower().endswith(".png"):
            mime = "image/png"
        elif fname.lower().endswith(".pdf"):
            mime = "application/pdf"

        try:
            with open(file_path, "rb") as f:
                data = {}
                if self._sender_bank_name:
                    data["sender_bank_name"] = self._sender_bank_name
                r = await self._client.post(
                    url,
                    files={"proofs[]": (fname, f, mime)},
                    data=data,
                    headers={"Authorization": f"Bearer {self._jwt}"},
                )
            if r.status_code not in (200, 201, 204):
                logger.warning("[%s] upload_receipt: %d — body: %s",
                               self.label, r.status_code, r.text[:300])
            else:
                logger.info("[%s] Receipt uploaded OK", self.label)
        except FileNotFoundError:
            raise RuntimeError(f"Receipt file not found: {file_path}")

    # ------------------------------------------------------------------ settings (existing API)

    _BANK_ALIAS: dict[str, str] = {
        "tinkoff": "tinkoff", "тинькофф": "tinkoff", "tink": "tinkoff",
        "sber": "sberbank", "сбер": "sberbank", "сбербанк": "sberbank",
        "alfa": "alfa_bank", "альфа": "alfa_bank",
        "vtb": "vtb", "втб": "vtb",
    }

    _TOGGLE_FIELD: dict[str, str] = {
        "phone": "accepts_sbp", "телефон": "accepts_sbp",
        "card": "accepts_card_to_card", "карта": "accepts_card_to_card",
        "account": "account_number_transfer_enabled", "счёт": "account_number_transfer_enabled",
        "executor": "enabled",
    }

    async def update_limits(self, new_min: int, new_max: int) -> None:
        r = await self._client.patch(
            f"{_API_BASE}/executor",
            json={"min_amount": new_min, "max_amount": new_max},
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        r.raise_for_status()
        logger.info("[%s] Limits updated: %d–%d", self.label, new_min, new_max)

    async def select_bank(self, bank_name: str) -> None:
        alias = self._BANK_ALIAS.get(bank_name.lower().strip())
        if not alias:
            raise ValueError(f"Unknown bank: {bank_name}")
        r = await self._client.patch(
            f"{_API_BASE}/executor",
            json={"bank_names": [alias]},
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        r.raise_for_status()
        logger.info("[%s] Bank set to %s", self.label, alias)

    async def toggle_setting(self, setting: str, enabled: bool) -> None:
        field = self._TOGGLE_FIELD.get(setting.lower().strip())
        if not field:
            raise ValueError(f"Unknown setting: {setting}")
        r = await self._client.patch(
            f"{_API_BASE}/executor",
            json={field: enabled},
            headers=self._auth_headers(),
        )
        if r.status_code == 401:
            raise TokenExpiredError()
        r.raise_for_status()
        logger.info("[%s] Toggle %s=%s", self.label, setting, enabled)
