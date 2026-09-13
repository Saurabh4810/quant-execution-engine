"""Kite WebSocket tick stream adapter implementing the TickStream protocol.

Design:
  - Wraps a bounded asyncio.Queue between the WebSocket producer and consumers.
  - On disconnect, notifies the caller via ConnectionError so live_runner can
    trigger reconnect and broker reconciliation.
  - Binary tick decoding follows the Kite Connect WebSocket binary packet spec
    (mode FULL: instrument_token[4] + last_price[4] + ... fields).
  - No live WebSocket library is imported here; inject a compatible client.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from .models import D

logger = logging.getLogger("quant_engine.kite_ws")


# ---------------------------------------------------------------------------
# Protocol — external WebSocket client must implement this
# ---------------------------------------------------------------------------

class WebSocketClient(Protocol):
    """Minimal interface for an injected WebSocket client."""
    async def connect(self, url: str, headers: dict) -> None: ...
    async def send(self, message: str | bytes) -> None: ...
    async def recv(self) -> bytes: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Binary tick decoder (Kite FULL mode)
# ---------------------------------------------------------------------------

def decode_binary_tick(data: bytes) -> list[dict]:
    """Decode a Kite binary WebSocket message into a list of tick dicts.

    Kite binary packet layout (FULL mode, per Kite Connect docs):
      offset  size  field
      0       2     number of packets
      2       2     packet length
      4       4     instrument_token  (big-endian int32)
      8       4     last_price        (int32 / 100.0)
      12      4     last_quantity
      16      4     average_price     (int32 / 100.0)
      20      4     volume
      24      4     buy_quantity
      28      4     sell_quantity
      32      8     open              (int32 / 100.0 each)
      40      8     close / prev_close
      ...
    This implementation decodes the minimal fields needed by LiveRunner.
    """
    if len(data) < 4:
        return []

    num_packets = struct.unpack_from(">H", data, 0)[0]
    ticks: list[dict] = []
    offset = 2

    for _ in range(num_packets):
        if offset + 2 > len(data):
            break
        pkt_len = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        pkt = data[offset: offset + pkt_len]
        offset += pkt_len

        if len(pkt) < 8:
            continue

        instrument_token = struct.unpack_from(">I", pkt, 0)[0]
        last_price_raw = struct.unpack_from(">I", pkt, 4)[0]
        last_price = Decimal(last_price_raw) / 100

        tick: dict = {
            "instrument_token": instrument_token,
            "last_price": last_price,
            "mode": "binary",
        }

        if len(pkt) >= 44:
            tick["last_quantity"] = struct.unpack_from(">I", pkt, 8)[0]
            tick["average_price"] = Decimal(struct.unpack_from(">I", pkt, 12)[0]) / 100
            tick["volume"] = struct.unpack_from(">I", pkt, 16)[0]
            tick["buy_quantity"] = struct.unpack_from(">I", pkt, 20)[0]
            tick["sell_quantity"] = struct.unpack_from(">I", pkt, 24)[0]
            tick["ohlc"] = {
                "open": Decimal(struct.unpack_from(">I", pkt, 28)[0]) / 100,
                "high": Decimal(struct.unpack_from(">I", pkt, 32)[0]) / 100,
                "low": Decimal(struct.unpack_from(">I", pkt, 36)[0]) / 100,
                "close": Decimal(struct.unpack_from(">I", pkt, 40)[0]) / 100,
            }

        ticks.append(tick)

    return ticks


# ---------------------------------------------------------------------------
# KiteTickStream — implements market_data.TickStream protocol
# ---------------------------------------------------------------------------

class KiteTickStream:
    """Kite WebSocket tick stream.

    Connects to wss://ws.kite.trade with Kite credentials, subscribes to
    *tokens*, and exposes ticks via next_tick().

    Args:
        api_key:       Kite API key.
        access_token:  Live session access token.
        ws_client:     Injected WebSocket client (e.g., websockets.connect).
        queue_maxsize: Back-pressure limit for the internal tick queue.
    """

    _WS_URL = "wss://ws.kite.trade"
    _SUBSCRIBE_MODE = "full"

    def __init__(
        self,
        api_key: str,
        access_token: str,
        ws_client: WebSocketClient,
        queue_maxsize: int = 512,
    ):
        self._api_key = api_key
        self._access_token = access_token
        self._ws = ws_client
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._tokens: list[int] = []
        self._connected = False
        self._recv_task: asyncio.Task | None = None

    async def connect(self, tokens: list[int]) -> None:
        """Connect to Kite WebSocket and subscribe to *tokens* in FULL mode."""
        self._tokens = tokens
        url = f"{self._WS_URL}?api_key={self._api_key}&access_token={self._access_token}"
        headers = {"X-Kite-Version": "3"}
        await self._ws.connect(url, headers)
        self._connected = True
        logger.info("kite_ws_connected tokens=%s", tokens)

        # Subscribe
        import json
        sub_msg = json.dumps({"a": "subscribe", "v": tokens})
        await self._ws.send(sub_msg)
        mode_msg = json.dumps({"a": "mode", "v": [self._SUBSCRIBE_MODE, tokens]})
        await self._ws.send(mode_msg)

        # Start background receiver
        self._recv_task = asyncio.create_task(self._receive_loop(), name="kite_ws_recv")

    async def next_tick(self) -> dict:
        """Return the next decoded tick dict.  Blocks until one is available.

        Raises ConnectionError if the stream has been closed.
        """
        while True:
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                self._queue.task_done()
                return tick
            except asyncio.TimeoutError:
                if not self._connected:
                    raise ConnectionError("KiteTickStream disconnected")

    async def close(self) -> None:
        """Close the WebSocket connection and stop the receiver."""
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        try:
            await self._ws.close()
        except Exception:
            pass
        logger.info("kite_ws_closed")

    async def _receive_loop(self) -> None:
        """Background task: receive raw bytes, decode, enqueue ticks."""
        try:
            while self._connected:
                raw = await self._ws.recv()
                if isinstance(raw, bytes) and len(raw) > 2:
                    ticks = decode_binary_tick(raw)
                    for tick in ticks:
                        try:
                            self._queue.put_nowait(tick)
                        except asyncio.QueueFull:
                            logger.warning("kite_ws_queue_full dropping_tick instrument=%s",
                                           tick.get("instrument_token"))
                elif isinstance(raw, str):
                    # Text frames are heartbeats or error messages
                    logger.debug("kite_ws_text_frame msg=%s", raw[:200])
        except (ConnectionError, OSError, asyncio.CancelledError):
            self._connected = False
            logger.info("kite_ws_recv_loop_ended")
