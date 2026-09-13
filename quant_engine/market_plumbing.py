"""Indian market plumbing: MCX contract table, NSE F&O margin model, rollover schedule.

All values sourced from exchange circulars (MCX, NSE).  Margin model is a stub
that must be replaced with live SPAN parameters from the exchange API before
any live deployment.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .models import Contract, ContractSpec, D


# ---------------------------------------------------------------------------
# MCX Contract Table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MCXProduct:
    symbol: str
    lot_size: int
    tick_size: Decimal
    price_basis: str
    delivery_months: tuple  # calendar months with active contracts
    near_far_spread: int = 1  # typical active near-month offset in months


# Staggered delivery schedule per MCX product
MCX_PRODUCTS: dict[str, MCXProduct] = {
    "CRUDEOIL": MCXProduct(
        symbol="CRUDEOIL", lot_size=100, tick_size=D("1"),
        price_basis="INR per barrel", delivery_months=tuple(range(1, 13)),
    ),
    "GOLD": MCXProduct(
        symbol="GOLD", lot_size=1, tick_size=D("1"),
        price_basis="INR per 10 grams", delivery_months=(2, 4, 6, 8, 10, 12),
    ),
    "SILVER": MCXProduct(
        symbol="SILVER", lot_size=30, tick_size=D("1"),
        price_basis="INR per kilogram", delivery_months=(3, 5, 7, 9, 12),
    ),
    "NATURALGAS": MCXProduct(
        symbol="NATURALGAS", lot_size=1250, tick_size=D("0.10"),
        price_basis="INR per mmBtu", delivery_months=tuple(range(1, 13)),
    ),
    "COPPER": MCXProduct(
        symbol="COPPER", lot_size=2500, tick_size=D("0.05"),
        price_basis="INR per kilogram", delivery_months=(2, 4, 6, 8, 11),
    ),
}


def mcx_expiry(symbol: str, year: int, month: int) -> str:
    """Return MCX expiry date (YYYY-MM-DD): last business day of the expiry month."""
    if month == 12:
        last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    while last_day.weekday() >= 5:  # roll back past weekend
        last_day -= datetime.timedelta(days=1)
    return last_day.strftime("%Y-%m-%d")


def mcx_active_contract(symbol: str, as_of: str) -> Contract:
    """Return the near-month active MCX Contract for *symbol* as of *as_of* (YYYY-MM-DD)."""
    product = MCX_PRODUCTS.get(symbol)
    if product is None:
        raise LookupError(f"Unknown MCX symbol: {symbol}")
    today = datetime.date.fromisoformat(as_of)
    for delta in range(12):
        candidate = (today.replace(day=1) + datetime.timedelta(days=32 * delta)).replace(day=1)
        if candidate.month in product.delivery_months:
            expiry_str = mcx_expiry(symbol, candidate.year, candidate.month)
            if datetime.date.fromisoformat(expiry_str) >= today:
                return Contract(
                    symbol=symbol, exchange="MCX",
                    lot_size=product.lot_size, tick_size=product.tick_size,
                    price_basis=product.price_basis, expiry=expiry_str,
                )
    raise LookupError(f"Could not determine active contract for MCX:{symbol}")


# ---------------------------------------------------------------------------
# NSE F&O Expiry Calendar
# ---------------------------------------------------------------------------

def nse_fo_expiry(year: int, month: int) -> str:
    """NSE F&O monthly expiry: last Thursday of the expiry month."""
    if month == 12:
        last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    while last_day.weekday() != 3:  # Thursday == 3
        last_day -= datetime.timedelta(days=1)
    return last_day.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# NSE F&O Margin Model (SPAN stub)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarginRequirement:
    span_margin: Decimal
    exposure_margin: Decimal
    total_margin: Decimal


def nse_fo_margin(contract: Contract, price: Decimal, lots: int = 1) -> MarginRequirement:
    """Approximate NSE F&O margin (SPAN stub).

    IMPORTANT: Replace with live SPAN margin files from NSE before deployment.
    Approximation: SPAN ≈ 8% + Exposure ≈ 3% of notional value.
    """
    notional = price * contract.lot_size * lots
    span = (notional * D("0.08")).quantize(D("0.01"))
    exposure = (notional * D("0.03")).quantize(D("0.01"))
    return MarginRequirement(span_margin=span, exposure_margin=exposure, total_margin=span + exposure)


# ---------------------------------------------------------------------------
# Rollover Schedule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RolloverWindow:
    rollover_start: str
    expiry: str


def nfo_rollover_window(year: int, month: int, days_before: int = 5) -> RolloverWindow:
    """Return the rollover window for an NSE F&O monthly contract.

    Rollover typically starts *days_before* calendar days before expiry.
    """
    expiry = nse_fo_expiry(year, month)
    start = (datetime.date.fromisoformat(expiry) - datetime.timedelta(days=days_before)).strftime("%Y-%m-%d")
    return RolloverWindow(rollover_start=start, expiry=expiry)
