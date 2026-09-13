"""Broker adapters: PaperBroker (deterministic fills) and KiteBroker (Zerodha REST).

Invariants:
  - place() is idempotent: submitting the same client_order_id twice returns
    the cached order without a second network call.
  - KiteBroker credentials come exclusively from environment variables.
  - Token refresh is triggered on HTTP 401 before re-raising.
  - HTTP 429 (rate limit) is retried with exponential backoff.
  - Never submit live orders without an explicit operator-controlled flag.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from decimal import Decimal

from .models import Contract, Order, OrderStatus, D

logger = logging.getLogger("quant_engine.broker")

_KITE_BASE = "https://api.kite.trade"
_MAX_RETRIES = 3
_BASE_DELAY = 0.5  # seconds


class Broker(ABC):
    @abstractmethod
    def place(self, order: Order, reference_price: Decimal) -> Order: ...

    @abstractmethod
    def orders(self) -> list[Order]: ...


class PaperBroker(Broker):
    def __init__(self, slippage_bps: Decimal = D(0)):
        self.slippage_bps = slippage_bps
        self._orders: dict[str, Order] = {}

    def place(self, order: Order, reference_price: Decimal) -> Order:
        if order.client_order_id in self._orders:
            return self._orders[order.client_order_id]
        order.contract.validate_qty(order.quantity)
        sign = D(1) if order.side == "BUY" else D(-1)
        order.fill_price = reference_price * (D(1) + sign * self.slippage_bps / D(10000))
        order.status = OrderStatus.FILLED
        self._orders[order.client_order_id] = order
        return order

    def orders(self) -> list[Order]:
        return list(self._orders.values())


class KiteBroker(Broker):
    """Zerodha Kite Connect REST adapter.

    Credentials are read from environment variables at construction time:
      KITE_API_KEY        — your Kite API key
      KITE_ACCESS_TOKEN   — session access token (refreshed automatically on 401)
      KITE_API_SECRET     — API secret for token refresh (used only if 401 received)

    Set KITE_LIVE_ORDERS=1 to enable real order placement; default is paper-safe
    (all place() calls raise RuntimeError if the flag is absent).
    """

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
        api_secret: str | None = None,
    ):
        self.api_key = api_key or os.environ["KITE_API_KEY"]
        self._access_token = access_token or os.environ["KITE_ACCESS_TOKEN"]
        self._api_secret = api_secret or os.environ.get("KITE_API_SECRET", "")
        self._live = os.environ.get("KITE_LIVE_ORDERS", "0") == "1"
        self._order_cache: dict[str, Order] = {}

    # ------------------------------------------------------------------
    # Broker interface
    # ------------------------------------------------------------------

    def place(self, order: Order, reference_price: Decimal) -> Order:
        """Place a limit order via Kite REST.

        Idempotent: duplicate client_order_id returns cached result.
        Requires KITE_LIVE_ORDERS=1 environment variable for live execution.
        """
        if order.client_order_id in self._order_cache:
            return self._order_cache[order.client_order_id]

        if not self._live:
            raise RuntimeError(
                "Live orders are disabled. Set KITE_LIVE_ORDERS=1 after paper-trading acceptance."
            )

        order.contract.validate_qty(order.quantity)
        payload = {
            "exchange": order.contract.exchange,
            "tradingsymbol": order.contract.symbol,
            "transaction_type": order.side,
            "quantity": order.quantity,
            "order_type": "LIMIT",
            "price": str(reference_price),
            "product": "NRML",  # Normal for F&O; override per instrument if needed
            "validity": "DAY",
            "tag": order.client_order_id[:20],  # Kite tag max 20 chars
        }
        resp = self._request("POST", "/orders/regular", payload)
        kite_order_id = resp.get("data", {}).get("order_id", "")
        order.status = OrderStatus.NEW  # confirmed placed; fill comes via WebSocket
        order.fill_price = None
        self._order_cache[order.client_order_id] = order
        logger.info("kite_order_placed client_id=%s kite_id=%s", order.client_order_id, kite_order_id)
        return order

    def orders(self) -> list[Order]:
        """Fetch all today's orders from Kite for reconciliation."""
        resp = self._request("GET", "/orders")
        raw_orders = resp.get("data", [])
        result: list[Order] = []
        for raw in raw_orders:
            tag = raw.get("tag", "")
            if not tag:
                continue
            # Reconstruct a minimal Order; full hydration needs ContractMaster
            from .models import Order as Ord, Contract as C
            contract = C(
                symbol=raw.get("tradingsymbol", ""),
                exchange=raw.get("exchange", "NSE"),
                lot_size=1,          # placeholder; hydrate from ContractMaster
                tick_size=D("0.05"),
            )
            ord_ = Ord(
                contract=contract,
                side=raw.get("transaction_type", "BUY"),
                quantity=int(raw.get("quantity", 0)),
                client_order_id=tag,
                status=OrderStatus(raw.get("status", "NEW")),
                fill_price=D(str(raw["average_price"])) if raw.get("average_price") else None,
            )
            result.append(ord_)
        return result

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Make an authenticated Kite REST call with retry and token refresh."""
        for attempt in range(_MAX_RETRIES):
            try:
                return self._do_request(method, path, payload)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    logger.warning("kite_401_refreshing_token attempt=%d", attempt)
                    self._refresh_token()
                    continue
                if exc.code == 429:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning("kite_429_rate_limited sleeping=%.2fs", delay)
                    time.sleep(delay)
                    continue
                raise
        raise RuntimeError(f"Kite request failed after {_MAX_RETRIES} attempts: {method} {path}")

    def _do_request(self, method: str, path: str, payload: dict | None) -> dict:
        url = _KITE_BASE + path
        headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self._access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = urllib.parse.urlencode(payload).encode() if payload else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _refresh_token(self) -> None:
        """Obtain a new access token using the request token flow.

        In production, the request_token arrives via the Kite login redirect.
        Store it securely and pass via KITE_REQUEST_TOKEN env var.
        """
        import hashlib
        request_token = os.environ.get("KITE_REQUEST_TOKEN", "")
        if not request_token:
            raise RuntimeError("KITE_REQUEST_TOKEN env var required for token refresh")
        checksum = hashlib.sha256(
            (self.api_key + request_token + self._api_secret).encode()
        ).hexdigest()
        payload = {
            "api_key": self.api_key,
            "request_token": request_token,
            "checksum": checksum,
        }
        resp = self._do_request("POST", "/session/token", payload)
        self._access_token = resp["data"]["access_token"]
        logger.info("kite_token_refreshed")
