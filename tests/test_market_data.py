import asyncio
import unittest
from quant_engine.market_data import ContractMaster, RetryPolicy, with_retry
from quant_engine.models import Contract, D

class MarketDataTests(unittest.TestCase):
    def test_contract_master_rolls_to_next_expiry(self):
        master = ContractMaster([
            Contract("GOLD", "MCX", 1, D(1), expiry="2026-09-15"),
            Contract("GOLD", "MCX", 1, D(1), expiry="2026-10-05"),
        ])
        self.assertEqual(master.rollover("GOLD", "MCX", "2026-09-16").expiry, "2026-10-05")
    def test_retries_transient_failure(self):
        calls = 0
        async def operation():
            nonlocal calls
            calls += 1
            if calls < 2: raise ConnectionError()
            return {"ok": True}
        self.assertEqual(asyncio.run(with_retry(operation, RetryPolicy(2, 0))), {"ok": True})
        self.assertEqual(calls, 2)
