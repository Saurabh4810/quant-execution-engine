from decimal import Decimal
from .broker import PaperBroker
from .execution import OrderService, StateStore
from .models import Contract, D

contract = Contract("NSE:DEMO", "NSE", 1, D("0.05"))
service = OrderService(PaperBroker(D(2)), StateStore())
order = service.submit(contract, "BUY", 1, D("100.00"), "demo-001")
print(f"paper fill: {order.fill_price}; position: {service.state.positions[contract.symbol]}")
