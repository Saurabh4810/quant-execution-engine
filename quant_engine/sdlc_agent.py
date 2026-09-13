"""SDLC regression agent: runs a canonical backtest and fails if metrics regress.

On every strategy change, this agent:
  1. Runs a deterministic, reproducible backtest with fixed seed data.
  2. Compares PnL, fills, and cost against a locked baseline.
  3. Exits non-zero if any metric is worse than baseline by more than the
     allowed tolerance, causing CI to fail.

Usage:
    python -m quant_engine.sdlc_agent              # compare against locked baseline
    python -m quant_engine.sdlc_agent --lock       # update the baseline file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from .backtest import Backtest
from .indicators import ATR
from .models import Bar, ContractSpec, D

# ---------------------------------------------------------------------------
# Canonical test fixtures (deterministic, no randomness)
# ---------------------------------------------------------------------------

_BASELINE_FILE = Path(__file__).parent.parent / "outputs" / "sdlc_baseline.json"


def _make_canonical_bars() -> list[Bar]:
    """Fixed synthetic bar series for reproducible regression testing."""
    prices = [
        5000, 5050, 5020, 5080, 5060, 5100, 5090, 5070, 5040, 5010,
        5030, 5060, 5090, 5120, 5110, 5100, 5080, 5060, 5040, 5020,
        5040, 5080, 5110, 5140, 5130, 5120, 5100, 5080, 5060, 5050,
        5070, 5100, 5120, 5150, 5140, 5130, 5110, 5090, 5070, 5060,
        5080, 5110, 5140, 5160, 5150, 5130, 5110, 5090, 5070, 5050,
    ]
    return [
        Bar(
            ts=i,
            open=D(p),
            high=D(p + 20),
            low=D(p - 10),
            close=D(p),
            volume=D(1000),
        )
        for i, p in enumerate(prices)
    ]


def _make_signal():
    """Deterministic signal: ATR-gated momentum crossover for regression testing."""
    atr = ATR(14)

    def signal(history: list[Bar], pos) -> str | None:
        bar = history[-1]
        atr.update(bar)
        if atr.value is None or len(history) < 15:
            return None
        # Simple: buy on rising close > prev close, sell otherwise
        if len(history) < 2:
            return None
        if history[-1].close > history[-2].close and pos.quantity <= 0:
            return "BUY"
        if history[-1].close < history[-2].close and pos.quantity >= 0:
            return "SELL"
        return None

    return signal


def run_canonical_backtest() -> dict:
    contract = ContractSpec.mcx_crudeoil("2025-01-31")
    bars = _make_canonical_bars()
    result = Backtest(
        contract=contract,
        slippage_bps=D(2),
        brokerage_per_order=D(20),
        tax_bps=D("0.01"),
    ).run(bars, _make_signal())
    return {
        "realized_pnl": str(result.realized_pnl),
        "costs": str(result.costs),
        "fills": result.fills,
    }


# ---------------------------------------------------------------------------
# Baseline lock / compare
# ---------------------------------------------------------------------------

def lock_baseline(metrics: dict) -> None:
    _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE_FILE.write_text(json.dumps(metrics, indent=2))
    print(f"Baseline locked: {_BASELINE_FILE}")
    print(json.dumps(metrics, indent=2))


def compare(current: dict, baseline: dict) -> bool:
    """Return True if current metrics are acceptable (no regression)."""
    ok = True
    for key in ["realized_pnl", "costs"]:
        cur_val = Decimal(current[key])
        base_val = Decimal(baseline[key])
        # Allow costs to increase by at most 5% (rounding changes)
        if key == "costs":
            if cur_val > base_val * D("1.05"):
                print(f"REGRESSION [{key}]: current={cur_val} baseline={base_val} (+>5%)")
                ok = False
            else:
                print(f"OK         [{key}]: current={cur_val} baseline={base_val}")
        # PnL must not drop below baseline
        elif key == "realized_pnl":
            if cur_val < base_val:
                print(f"REGRESSION [{key}]: current={cur_val} baseline={base_val} (dropped)")
                ok = False
            else:
                print(f"OK         [{key}]: current={cur_val} baseline={base_val}")
    # Fill count must be identical (structural regression indicator)
    if current["fills"] != baseline["fills"]:
        print(f"REGRESSION [fills]: current={current['fills']} baseline={baseline['fills']}")
        ok = False
    else:
        print(f"OK         [fills]: {current['fills']}")
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SDLC regression agent")
    parser.add_argument("--lock", action="store_true",
                        help="Lock current metrics as the new baseline")
    args = parser.parse_args()

    print("Running canonical backtest...")
    metrics = run_canonical_backtest()

    if args.lock:
        lock_baseline(metrics)
        sys.exit(0)

    if not _BASELINE_FILE.exists():
        print(f"No baseline found at {_BASELINE_FILE}. Run with --lock to create one.")
        lock_baseline(metrics)
        sys.exit(0)

    baseline = json.loads(_BASELINE_FILE.read_text())
    print(f"\nCurrent:  {metrics}")
    print(f"Baseline: {baseline}\n")

    passed = compare(metrics, baseline)
    if passed:
        print("\n✅ All metrics within tolerance — no regression detected.")
        sys.exit(0)
    else:
        print("\n❌ Regression detected — failing CI.")
        sys.exit(1)


if __name__ == "__main__":
    main()
