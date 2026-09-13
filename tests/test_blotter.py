"""Unit tests for Blotter and deviation checking."""
import json
import unittest

from quant_engine.blotter import Blotter, deviation_check
from quant_engine.models import D


class BlotterTests(unittest.TestCase):
    def test_record_and_snapshot(self):
        blotter = Blotter()
        self.assertEqual(blotter.fill_count(), 0)

        entry = blotter.record(
            symbol="CRUDEOIL",
            exchange="MCX",
            side="BUY",
            quantity=100,
            fill_price=D("6500.00"),
            cost=D("25.50"),
            client_order_id="cid-1",
            strategy_tag="grid",
        )
        self.assertEqual(blotter.fill_count(), 1)
        self.assertEqual(entry.symbol, "CRUDEOIL")
        self.assertEqual(entry.fill_price, D("6500.00"))
        self.assertEqual(entry.cost, D("25.50"))
        self.assertEqual(blotter.total_realized_cost(), D("25.50"))

    def test_csv_export(self):
        blotter = Blotter()
        blotter.record("GOLD", "MCX", "BUY", 1, D("70000"), D("50"), "cid-g1")
        csv_out = blotter.to_csv()
        self.assertIn("GOLD", csv_out)
        self.assertIn("70000", csv_out)
        self.assertIn("cid-g1", csv_out)

    def test_json_export(self):
        blotter = Blotter()
        blotter.record("NIFTY", "NSE", "SELL", 50, D("22000"), D("30"), "cid-n1")
        json_str = blotter.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["symbol"], "NIFTY")
        self.assertEqual(parsed[0]["quantity"], 50)

    def test_deviation_check(self):
        blotter = Blotter()
        blotter.record("CRUDEOIL", "MCX", "BUY", 100, D("6500"), D("20"), "c1")
        blotter.record("CRUDEOIL", "MCX", "BUY", 100, D("6450"), D("20"), "c2")

        # Expected 2, actual 2 -> no deviation
        res = deviation_check(2, blotter, "CRUDEOIL")
        self.assertFalse(res.is_deviation)
        self.assertEqual(res.fill_delta, 0)

        # Expected 3, actual 2 -> deviation
        res2 = deviation_check(3, blotter, "CRUDEOIL", tolerance=0)
        self.assertTrue(res2.is_deviation)
        self.assertEqual(res2.fill_delta, -1)

        # Expected 3, actual 2 with tolerance 1 -> no deviation
        res3 = deviation_check(3, blotter, "CRUDEOIL", tolerance=1)
        self.assertFalse(res3.is_deviation)
