from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Literal
from uuid import uuid4

Money = Decimal
Side = Literal["BUY", "SELL"]


def D(value: str | int | float | Decimal) -> Decimal:
    """Convert through str so floats never leak binary error into money."""
    return Decimal(str(value))


def paisa(value: Decimal) -> Decimal:
    return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Contract:
    symbol: str
    exchange: Literal["NSE", "NFO", "MCX"]
    lot_size: int
    tick_size: Decimal
    price_basis: str = "INR per unit"
    expiry: str | None = None

    def valid_price(self, price: Decimal) -> bool:
        return price % self.tick_size == 0

    def validate_qty(self, quantity: int) -> None:
        if quantity <= 0 or quantity % self.lot_size:
            raise ValueError(f"quantity must be a positive multiple of lot size {self.lot_size}")


@dataclass(frozen=True)
class Bar:
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = D(0)


class OrderStatus(str, Enum):
    NEW = "NEW"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    contract: Contract
    side: Side
    quantity: int
    client_order_id: str = field(default_factory=lambda: str(uuid4()))
    status: OrderStatus = OrderStatus.NEW
    fill_price: Decimal | None = None


@dataclass
class Position:
    quantity: int = 0
    average_price: Decimal = D(0)
    realized_pnl: Decimal = D(0)

    def apply_fill(self, side: Side, qty: int, price: Decimal) -> None:
        signed = qty if side == "BUY" else -qty
        old = self.quantity
        if old == 0 or (old > 0) == (signed > 0):
            total = abs(old) * self.average_price + abs(signed) * price
            self.quantity = old + signed
            self.average_price = total / abs(self.quantity)
            return
        closing = min(abs(old), abs(signed))
        direction = D(1) if old > 0 else D(-1)
        self.realized_pnl = paisa(self.realized_pnl + (price - self.average_price) * closing * direction)
        self.quantity = old + signed
        self.average_price = price if self.quantity and (old > 0) != (self.quantity > 0) else (D(0) if not self.quantity else self.average_price)


# ---------------------------------------------------------------------------
# Indian market charge model
# ---------------------------------------------------------------------------

def exchange_charges(
    exchange: Literal["NSE", "NFO", "MCX"],
    transaction_value: Decimal,
    is_futures: bool = True,
) -> dict[str, Decimal]:
    """Return paisa-exact breakdown of statutory charges for a single-leg fill.

    NSE/NFO:
      - STT on sell side only for futures: 0.0125 % of transaction value
      - Exchange txn charge: 0.0019 % (NFO futures)
    MCX:
      - CTT (Commodities Transaction Tax) on sell: 0.01 % of transaction value
      - Exchange txn charge: 0.0026 %
    Brokerage is platform-dependent and excluded here; add it separately.

    All rates are per SEBI / FMC circulars and rounded to the paisa.
    """
    charges: dict[str, Decimal] = {}

    if exchange in ("NSE", "NFO"):
        # STT — futures sell side only
        stt_rate = D("0.000125")  # 0.0125%
        charges["stt"] = paisa(transaction_value * stt_rate)
        # Exchange transaction charge
        charges["exchange_txn"] = paisa(transaction_value * D("0.000019"))
    elif exchange == "MCX":
        # CTT — commodity futures sell side
        ctt_rate = D("0.0001")  # 0.01%
        charges["ctt"] = paisa(transaction_value * ctt_rate)
        charges["exchange_txn"] = paisa(transaction_value * D("0.000026"))

    # GST @ 18% on exchange transaction charge (common to all)
    txn = charges.get("exchange_txn", D(0))
    charges["gst"] = paisa(txn * D("0.18"))
    charges["total"] = paisa(sum(charges.values(), D(0)))
    return charges


# ---------------------------------------------------------------------------
# Standard Indian contract specifications
# ---------------------------------------------------------------------------

class ContractSpec:
    """Pre-filled Contract objects for the most common MCX and NSE F&O instruments.

    Expiry must be supplied by the caller from the live contract master; these
    specs capture the *structural* fields (lot size, tick size, price basis) so
    they are never duplicated across the codebase.
    """

    # --- MCX -----------------------------------------------------------------
    @staticmethod
    def mcx_crudeoil(expiry: str) -> Contract:
        """Crude Oil: 100 bbl lot, ₹1/bbl tick, INR/bbl quotation."""
        return Contract("CRUDEOIL", "MCX", lot_size=100, tick_size=D("1"),
                        price_basis="INR per barrel", expiry=expiry)

    @staticmethod
    def mcx_gold(expiry: str) -> Contract:
        """Gold (1 kg): 1 lot = 1 kg, ₹1/10g tick (i.e. ₹100/kg)."""
        return Contract("GOLD", "MCX", lot_size=1, tick_size=D("1"),
                        price_basis="INR per 10 grams", expiry=expiry)

    @staticmethod
    def mcx_silver(expiry: str) -> Contract:
        """Silver (30 kg): 1 lot = 30 kg, ₹1/kg tick."""
        return Contract("SILVER", "MCX", lot_size=30, tick_size=D("1"),
                        price_basis="INR per kilogram", expiry=expiry)

    @staticmethod
    def mcx_naturalgas(expiry: str) -> Contract:
        """Natural Gas: 1250 mmBtu lot, ₹0.10/mmBtu tick."""
        return Contract("NATURALGAS", "MCX", lot_size=1250, tick_size=D("0.10"),
                        price_basis="INR per mmBtu", expiry=expiry)

    # --- NSE F&O -------------------------------------------------------------
    @staticmethod
    def nfo_nifty(expiry: str) -> Contract:
        """NIFTY 50 futures: 75-unit lot, ₹0.05 tick."""
        return Contract("NIFTY", "NFO", lot_size=75, tick_size=D("0.05"),
                        price_basis="INR per index point", expiry=expiry)

    @staticmethod
    def nfo_banknifty(expiry: str) -> Contract:
        """BANK NIFTY futures: 35-unit lot, ₹0.05 tick."""
        return Contract("BANKNIFTY", "NFO", lot_size=35, tick_size=D("0.05"),
                        price_basis="INR per index point", expiry=expiry)

    @staticmethod
    def nfo_finnifty(expiry: str) -> Contract:
        """FIN NIFTY futures: 65-unit lot, ₹0.05 tick."""
        return Contract("FINNIFTY", "NFO", lot_size=65, tick_size=D("0.05"),
                        price_basis="INR per index point", expiry=expiry)
