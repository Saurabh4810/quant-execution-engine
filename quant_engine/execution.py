from dataclasses import dataclass, field
from decimal import Decimal
from .broker import Broker
from .models import Contract, Order, Position, Side

@dataclass
class StateStore:
    orders: dict[str, Order] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)

class OrderService:
    def __init__(self, broker: Broker, state: StateStore): self.broker, self.state = broker, state
    def submit(self, contract: Contract, side: Side, quantity: int, price: Decimal, client_order_id: str) -> Order:
        if client_order_id in self.state.orders: return self.state.orders[client_order_id]
        order = self.broker.place(Order(contract, side, quantity, client_order_id), price)
        self.state.orders[client_order_id] = order
        if order.fill_price is not None:
            pos = self.state.positions.setdefault(contract.symbol, Position())
            pos.apply_fill(side, quantity, order.fill_price)
        return order
    def reconcile(self) -> None:
        """Recovery: broker is authoritative for missing known order ids."""
        for order in self.broker.orders(): self.state.orders.setdefault(order.client_order_id, order)
