"""Thin async wrapper around the irMarket Buyer API.

Docs: https://api.irmarket.store/buyer/docs
"""
from __future__ import annotations

import httpx
import config


class IrMarketError(Exception):
    def __init__(self, status_code: int, message: str, payload: dict | None = None):
        super().__init__(f"irMarket API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}


class IrMarketClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or config.IRMARKET_BASE_URL).rstrip("/")
        self.api_key = api_key or config.IRMARKET_API_KEY
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def aclose(self):
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = await self._client.request(method, path, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            message = data.get("message") or data.get("error") or resp.text
            raise IrMarketError(resp.status_code, message, data)
        return data

    # ------------------------------------------------------------- reads --
    async def get_products(self) -> list[dict]:
        data = await self._request("GET", "/api/buyer/products")
        # API may return either a bare list or {"products": [...]}
        if isinstance(data, dict):
            return data.get("products") or data.get("data") or []
        return data

    async def get_balance(self) -> dict:
        return await self._request("GET", "/api/buyer/balance")

    async def get_me(self) -> dict:
        return await self._request("GET", "/api/buyer/me")

    async def get_order(self, order_id: int) -> dict:
        return await self._request("GET", f"/api/buyer/orders/{order_id}")

    # ------------------------------------------------------------ writes --
    async def purchase(
        self,
        product_id: int,
        quantity: int,
        idempotency_key: str,
        customer_email: str | None = None,
    ) -> dict:
        body = {
            "product_id": product_id,
            "quantity": quantity,
            "idempotency_key": idempotency_key,
        }
        if customer_email:
            body["customer_email"] = customer_email
        return await self._request("POST", "/api/buyer/purchase", json=body)

    async def register_webhook(self, url: str) -> dict:
        """Registers (or rotates) the webhook. Returns dict containing the secret."""
        return await self._request("POST", "/api/buyer/webhook", json={"url": url})


# Single shared instance used throughout the bot process.
client = IrMarketClient()
