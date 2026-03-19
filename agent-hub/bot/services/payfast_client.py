"""Payfast REST client for inbound payment monitoring."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class PayfastClient:
    BASE = "https://payfast.website/api"

    def __init__(self, config: dict) -> None:
        self._api_key: str = config.get("api_key", "")
        self._trader_id: str = config.get("trader_id", "")
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def get_orders(self) -> list[dict]:
        """GET /get_orders_trader → response["orders"]."""
        try:
            r = await self._client.get(
                f"{self.BASE}/get_orders_trader",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json().get("orders") or []
        except Exception as exc:
            logger.warning("Payfast get_orders failed: %s", exc)
            return []

    async def create_order(self, requisite: str, bank: str, amount: float) -> str:
        """Create inbound order on Payfast. Returns order_id (uuid_system).

        TODO: confirm exact endpoint + params via DevTools on payfast.website.
        Likely: POST /add_requisite with {requisite, bank, amount, trader_id}.
        """
        # TODO: implement after DevTools sniffing
        raise NotImplementedError(
            "Payfast create_order: endpoint TBD — sniff via DevTools on payfast.website"
        )

    async def cancel_order(self, order_id: str) -> None:
        """Cancel a Payfast order.

        TODO: confirm exact endpoint via DevTools.
        """
        logger.warning("Payfast cancel_order stub (order_id=%s)", order_id)
        # TODO: implement

    async def confirm_order(self, order_id: str) -> None:
        """Confirm a Payfast order.

        TODO: confirm exact endpoint via DevTools.
        """
        logger.warning("Payfast confirm_order stub (order_id=%s)", order_id)
        # TODO: implement

    async def close(self) -> None:
        await self._client.aclose()
