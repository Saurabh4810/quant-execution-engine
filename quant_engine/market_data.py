"""Zerodha-compatible REST/WebSocket abstractions; transport is injected, never global."""
from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from .models import Contract

class RestTransport(Protocol):
    async def request(self, method: str, path: str, payload: dict | None = None) -> dict: ...

class TickStream(Protocol):
    async def connect(self, tokens: list[int]) -> None: ...
    async def next_tick(self) -> dict: ...
    async def close(self) -> None: ...

@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_seconds: float = 0.25

async def with_retry(operation: Callable[[], Awaitable[dict]], policy: RetryPolicy = RetryPolicy()) -> dict:
    """Bounded exponential retry; callers should additionally enforce vendor rate limits."""
    for attempt in range(policy.attempts):
        try: return await operation()
        except (TimeoutError, ConnectionError):
            if attempt == policy.attempts - 1: raise
            await asyncio.sleep(policy.base_delay_seconds * (2 ** attempt))
    raise AssertionError("unreachable")

class ContractMaster:
    def __init__(self, contracts: list[Contract]): self.contracts = contracts
    def active(self, symbol: str, exchange: str, as_of: str) -> Contract:
        candidates = [c for c in self.contracts if c.symbol == symbol and c.exchange == exchange and (c.expiry is None or c.expiry >= as_of)]
        if not candidates: raise LookupError(f"no active contract for {exchange}:{symbol}")
        return min(candidates, key=lambda c: c.expiry or "9999-12-31")
    def rollover(self, symbol: str, exchange: str, as_of: str) -> Contract:
        return self.active(symbol, exchange, as_of)
