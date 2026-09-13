"""Tests for Indian market plumbing (MCX and NSE F&O specifics)."""
from decimal import Decimal
import unittest

from quant_engine.market_plumbing import (
    MCX_PRODUCTS,
    mcx_active_contract,
    mcx_expiry,
    nse_fo_expiry,
    nse_fo_margin,
    nfo_rollover_window,
)
from quant_engine.models import Contract, ContractSpec, D, exchange_charges


class MarketPlumbingTests(unittest.TestCase):
    def test_mcx_products_table_defined(self):
        for sym in ("CRUDEOIL", "GOLD", "SILVER", "NATURALGAS", "COPPER"):
            self.assertIn(sym, MCX_PRODUCTS)
            prod = MCX_PRODUCTS[sym]
            self.assertGreater(prod.lot_size, 0)
            self.assertGreater(prod.tick_size, Decimal(0))
            self.assertGreater(len(prod.delivery_months), 0)

    def test_mcx_expiry_not_on_weekend(self):
        # Last business day of month
        for year in (2024, 2025, 2026):
            for month in (1, 3, 5, 8, 12):
                exp = mcx_expiry("CRUDEOIL", year, month)
                import datetime
                dt = datetime.date.fromisoformat(exp)
                self.assertLess(dt.weekday(), 5, f"Expiry {exp} falls on a weekend")

    def test_mcx_active_contract_resolution(self):
        contract = mcx_active_contract("CRUDEOIL", "2026-03-01")
        self.assertEqual(contract.symbol, "CRUDEOIL")
        self.assertEqual(contract.exchange, "MCX")
        self.assertEqual(contract.lot_size, 100)
        self.assertEqual(contract.tick_size, D("1"))
        self.assertGreaterEqual(contract.expiry, "2026-03-01")

    def test_mcx_active_contract_unknown_symbol_raises(self):
        with self.assertRaises(LookupError):
            mcx_active_contract("NONEXISTENT", "2026-03-01")

    def test_nse_fo_expiry_is_thursday(self):
        for year in (2025, 2026):
            for month in range(1, 13):
                exp = nse_fo_expiry(year, month)
                import datetime
                dt = datetime.date.fromisoformat(exp)
                self.assertEqual(dt.weekday(), 3, f"Expiry {exp} is not a Thursday")

    def test_nfo_rollover_window(self):
        window = nfo_rollover_window(2026, 3, days_before=5)
        self.assertLess(window.rollover_start, window.expiry)

    def test_nse_fo_margin_calculation(self):
        contract = ContractSpec.nfo_nifty("2026-03-26")
        price = D("22000")
        margin = nse_fo_margin(contract, price, lots=2)
        notional = price * contract.lot_size * 2
        expected_span = (notional * D("0.08")).quantize(D("0.01"))
        expected_exposure = (notional * D("0.03")).quantize(D("0.01"))
        self.assertEqual(margin.span_margin, expected_span)
        self.assertEqual(margin.exposure_margin, expected_exposure)
        self.assertEqual(margin.total_margin, expected_span + expected_exposure)

    def test_exchange_charges_mcx_and_nse(self):
        val = D("100000")
        mcx_chg = exchange_charges("MCX", val, is_futures=True)
        self.assertIn("ctt", mcx_chg)
        self.assertIn("exchange_txn", mcx_chg)
        self.assertIn("gst", mcx_chg)
        self.assertIn("total", mcx_chg)
        self.assertEqual(mcx_chg["ctt"], D("10.00"))  # 0.01% of 100000

        nse_chg = exchange_charges("NSE", val, is_futures=True)
        self.assertIn("stt", nse_chg)
        self.assertEqual(nse_chg["stt"], D("12.50"))  # 0.0125% of 100000
