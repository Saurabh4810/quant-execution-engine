"""Trade blotter: append-only fill log, deviation detection, and CSV export.

The blotter is the authoritative record of every fill that the engine has
processed.  It is intentionally append-only (no edits) and is the primary
source of truth for desk reconciliation.
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from .models import D, paisa
from .runtime import log_event


@dataclass(frozen=True)
class BlotterEntry:
    ts: float            # Unix timestamp of the fill
    symbol: str
    exchange: str
    side: str
    quantity: int
    fill_price: Decimal
    cost: Decimal        # Slippage + brokerage + tax, paisa-exact
    client_order_id: str
    strategy_tag: str = ""


class Blotter:
    """Append-only structured fill log.

    Thread-safety: single-threaded asyncio; use asyncio.Lock if sharing
    across tasks.
    """

    def __init__(self) -> None:
        self._entries: list[BlotterEntry] = []

    def record(
        self,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        fill_price: Decimal,
        cost: Decimal,
        client_order_id: str,
        strategy_tag: str = "",
    ) -> BlotterEntry:
        """Append a fill to the blotter and emit a structured log event."""
        entry = BlotterEntry(
            ts=time.time(),
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            fill_price=fill_price,
            cost=paisa(cost),
            client_order_id=client_order_id,
            strategy_tag=strategy_tag,
        )
        self._entries.append(entry)
        log_event(
            "blotter_fill",
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            fill_price=str(fill_price),
            cost=str(cost),
            client_order_id=client_order_id,
            strategy_tag=strategy_tag,
        )
        return entry

    def entries(self) -> list[BlotterEntry]:
        """Return a snapshot of all recorded fills (immutable list)."""
        return list(self._entries)

    def to_csv(self) -> str:
        """Export all fills as a CSV string for desk reconciliation."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "ts", "symbol", "exchange", "side", "quantity",
            "fill_price", "cost", "client_order_id", "strategy_tag",
        ])
        for e in self._entries:
            writer.writerow([
                e.ts, e.symbol, e.exchange, e.side, e.quantity,
                e.fill_price, e.cost, e.client_order_id, e.strategy_tag,
            ])
        return buf.getvalue()

    def to_json(self) -> str:
        """Export all fills as a JSON array string."""
        rows = [
            {
                "ts": e.ts,
                "symbol": e.symbol,
                "exchange": e.exchange,
                "side": e.side,
                "quantity": e.quantity,
                "fill_price": str(e.fill_price),
                "cost": str(e.cost),
                "client_order_id": e.client_order_id,
                "strategy_tag": e.strategy_tag,
            }
            for e in self._entries
        ]
        return json.dumps(rows, indent=2)

    def total_realized_cost(self) -> Decimal:
        return paisa(sum((e.cost for e in self._entries), D(0)))

    def fill_count(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Deviation Detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviationResult:
    symbol: str
    expected_fills: int
    actual_fills: int
    fill_delta: int
    is_deviation: bool
    description: str


def deviation_check(
    expected_fills: int,
    blotter: Blotter,
    symbol: str,
    tolerance: int = 0,
) -> DeviationResult:
    """Alert if the engine's actual fill count deviates from the expected count.

    Args:
        expected_fills:  Number of fills the strategy model expects.
        blotter:         Live blotter instance.
        symbol:          Instrument symbol to filter.
        tolerance:       Allowed absolute difference before flagging deviation.

    Returns:
        DeviationResult with is_deviation=True if |delta| > tolerance.
    """
    actual = sum(1 for e in blotter.entries() if e.symbol == symbol)
    delta = actual - expected_fills
    is_dev = abs(delta) > tolerance
    desc = (
        f"{'DEVIATION' if is_dev else 'OK'}: expected={expected_fills} "
        f"actual={actual} delta={delta:+d} symbol={symbol}"
    )
    if is_dev:
        log_event("blotter_deviation_detected", symbol=symbol,
                  expected=expected_fills, actual=actual, delta=delta)
    return DeviationResult(
        symbol=symbol,
        expected_fills=expected_fills,
        actual_fills=actual,
        fill_delta=delta,
        is_deviation=is_dev,
        description=desc,
    )
