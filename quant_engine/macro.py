from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from .models import D

class Regime(str, Enum): RISK_ON="RISK_ON"; NEUTRAL="NEUTRAL"; RISK_OFF="RISK_OFF"

@dataclass(frozen=True)
class RegimeDecision:
    regime: Regime
    score: Decimal
    spacing_multiplier: Decimal
    trading_enabled: bool

class MacroRegimeEngine:
    """Proxies are normalized [-1, 1]; positive supports risk assets."""
    def decide(self, proxies: dict[str, Decimal]) -> RegimeDecision:
        if not proxies: return RegimeDecision(Regime.NEUTRAL, D(0), D(1), True)
        score = sum(proxies.values(), D(0)) / len(proxies)
        if score <= D("-0.60"): return RegimeDecision(Regime.RISK_OFF, score, D("1.75"), False)
        if score >= D("0.25"): return RegimeDecision(Regime.RISK_ON, score, D("0.80"), True)
        return RegimeDecision(Regime.NEUTRAL, score, D(1), True)
