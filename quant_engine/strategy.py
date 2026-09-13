from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from .models import D, Position, Side

@dataclass(frozen=True)
class RiskLimits:
    position_cap: int
    max_daily_loss: Decimal
    atr_spacing_multiple: Decimal = D(1)
    pyramid_limit: int = 3

class RiskGate:
    def __init__(self, limits: RiskLimits): self.limits, self.halted = limits, False
    def permit(self, position: Position, side: Side, quantity: int, daily_pnl: Decimal) -> bool:
        if daily_pnl <= -self.limits.max_daily_loss: self.halted = True
        projected = position.quantity + (quantity if side == "BUY" else -quantity)
        return not self.halted and abs(projected) <= self.limits.position_cap

class GridEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.anchor: Decimal | None = None
        self.levels = 0
    def signal(self, price: Decimal, atr: Decimal, position: Position) -> Optional[Side]:
        if not atr or atr <= 0: return None
        if self.anchor is None: self.anchor = price; return None
        spacing = atr * self.limits.atr_spacing_multiple
        if self.levels >= self.limits.pyramid_limit: return None
        if price <= self.anchor - spacing and position.quantity <= 0: self.levels += 1; self.anchor = price; return "BUY"
        if price >= self.anchor + spacing and position.quantity >= 0: self.levels += 1; self.anchor = price; return "SELL"
        return None

class StopAndReverseEngine:
    def __init__(self, stop_atr_multiple: Decimal = D(2)): self.stop_atr_multiple = stop_atr_multiple
    def signal(self, price: Decimal, atr: Decimal, position: Position) -> Optional[Side]:
        if not position.quantity or not atr: return None
        stop = self.stop_atr_multiple * atr
        if position.quantity > 0 and price <= position.average_price - stop: return "SELL"
        if position.quantity < 0 and price >= position.average_price + stop: return "BUY"
        return None
