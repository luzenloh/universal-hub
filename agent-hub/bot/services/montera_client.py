"""Montera REST client for inbound payment monitoring."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class MonteraClient:
    BASE = "https://montera.one/api/merchant"

    def __init__(self, config: dict) -> None:
        self._api_key: str = config.get("api_key", "")
        self._merchant_id: str = config.get("merchant_id", "")
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def create_order(self, requisite: str, amount: float, currency: str = "RUB") -> str:
        """POST /order → response["uuid"].

        TODO: confirm exact params structure via DevTools on montera.one.
        """
        r = await self._client.post(
            f"{self.BASE}/order",
            json={
                "merchant_id": self._merchant_id,
                "requisite": requisite,
                "amount": amount,
                "currency": currency,
            },
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()["uuid"]

    async def get_orders(self) -> list[dict]:
        """GET /order → list of orders."""
        try:
            r = await self._client.get(
                f"{self.BASE}/order",
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                return data
            return data.get("orders") or data.get("data") or []
        except Exception as exc:
            logger.warning("Montera get_orders failed: %s", exc)
            return []

    async def cancel_order(self, order_id: str) -> None:
        """Cancel a Montera order.

        TODO: confirm exact endpoint via DevTools.
        """
        logger.warning("Montera cancel_order stub (order_id=%s)", order_id)
        # TODO: implement

    async def confirm_order(self, order_id: str) -> None:
        """Confirm a Montera order (trader-side confirmation).

        TODO: confirm exact endpoint via DevTools.
        """
        logger.warning("Montera confirm_order stub (order_id=%s)", order_id)
        # TODO: implement

    async def close(self) -> None:
        await self._client.aclose()
