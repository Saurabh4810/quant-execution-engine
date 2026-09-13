import unittest
from quant_engine.broker import PaperBroker
from quant_engine.execution import OrderService, StateStore
from quant_engine.models import Contract, D

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.contract = Contract("NSE:ABC", "NSE", 25, D("0.05"))
        self.service = OrderService(PaperBroker(D(10)), StateStore())
    def test_idempotency_prevents_duplicate_fill(self):
        a = self.service.submit(self.contract, "BUY", 25, D(100), "key-1")
        b = self.service.submit(self.contract, "BUY", 25, D(100), "key-1")
        self.assertIs(a, b)
        self.assertEqual(self.service.state.positions[self.contract.symbol].quantity, 25)
    def test_rejects_invalid_lot(self):
        with self.assertRaises(ValueError): self.service.submit(self.contract, "BUY", 1, D(100), "key-2")
    def test_realized_pnl_is_paisa_exact(self):
        service = OrderService(PaperBroker(D(0)), StateStore())
        service.submit(self.contract, "BUY", 25, D("100.00"), "buy")
        service.submit(self.contract, "SELL", 25, D("100.10"), "sell")
        self.assertEqual(service.state.positions[self.contract.symbol].realized_pnl, D("2.50"))
