"""Unit tests for SQLite persistence layer."""
from decimal import Decimal
import unittest

from quant_engine.models import Bar, Contract, D, Order, OrderStatus, Position
from quant_engine.storage import OHLCVStore, OrderRepository, PositionRepository


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.contract = Contract("CRUDEOIL", "MCX", 100, D("1"))

    def test_order_repository_save_and_load(self):
        repo = OrderRepository(":memory:")
        order1 = Order(self.contract, "BUY", 100, client_order_id="ord-001")
        order1.status = OrderStatus.FILLED
        order1.fill_price = D("6540.00")

        repo.save(order1)
        orders = repo.load_all()
        self.assertEqual(len(orders), 1)
        loaded = orders[0]
        self.assertEqual(loaded.client_order_id, "ord-001")
        self.assertEqual(loaded.contract.symbol, "CRUDEOIL")
        self.assertEqual(loaded.contract.exchange, "MCX")
        self.assertEqual(loaded.quantity, 100)
        self.assertEqual(loaded.status, OrderStatus.FILLED)
        self.assertEqual(loaded.fill_price, D("6540.00"))

        # Test upsert (update existing order status)
        order1.status = OrderStatus.REJECTED
        repo.save(order1)
        orders = repo.load_all()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].status, OrderStatus.REJECTED)
        repo.close()

    def test_position_repository_save_load(self):
        repo = PositionRepository(":memory:")
        pos = Position(quantity=200, average_price=D("6500.50"), realized_pnl=D("12500.25"))
        repo.save("CRUDEOIL", pos)

        loaded = repo.load("CRUDEOIL")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.quantity, 200)
        self.assertEqual(loaded.average_price, D("6500.50"))
        self.assertEqual(loaded.realized_pnl, D("12500.25"))

        # Non-existent symbol
        self.assertIsNone(repo.load("NIFTY"))

        # Load all
        repo.save("GOLD", Position(quantity=1, average_price=D("72000.00")))
        all_pos = repo.load_all()
        self.assertEqual(len(all_pos), 2)
        self.assertIn("CRUDEOIL", all_pos)
        self.assertIn("GOLD", all_pos)
        repo.close()

    def test_ohlcv_store_insert_and_query(self):
        store = OHLCVStore(":memory:")
        bars = [
            Bar(ts=1000, open=D("100"), high=D("105"), low=D("99"), close=D("103"), volume=D("500")),
            Bar(ts=2000, open=D("103"), high=D("108"), low=D("102"), close=D("107"), volume=D("600")),
            Bar(ts=3000, open=D("107"), high=D("110"), low=D("106"), close=D("109"), volume=D("400")),
        ]
        store.insert_bars("TEST_SYM", bars)
        self.assertEqual(store.bar_count("TEST_SYM"), 3)

        # Idempotent insert (INSERT OR IGNORE)
        store.insert_bars("TEST_SYM", bars)
        self.assertEqual(store.bar_count("TEST_SYM"), 3)

        # Load all
        loaded = store.load_bars("TEST_SYM")
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[0].ts, 1000)
        self.assertEqual(loaded[0].open, D("100"))
        self.assertEqual(loaded[2].close, D("109"))

        # Range query
        range_loaded = store.load_bars("TEST_SYM", from_ts=1500, to_ts=2500)
        self.assertEqual(len(range_loaded), 1)
        self.assertEqual(range_loaded[0].ts, 2000)

        store.close()
