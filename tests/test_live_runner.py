"""Integration tests for LiveRunner."""
import asyncio
from decimal import Decimal
import unittest

from quant_engine.broker import PaperBroker
from quant_engine.live_runner import LiveRunner
from quant_engine.models import Contract, D
from quant_engine.strategy import RiskLimits


class MockTickStream:
    def __init__(self, ticks: list[dict]):
        self.ticks = list(ticks)
        self.connected = False

    async def connect(self, instrument_tokens: list[int]) -> None:
        self.connected = True

    async def next_tick(self) -> dict:
        if not self.ticks:
            # Sleep indefinitely until cancelled/stopped
            await asyncio.sleep(10)
            return {"last_price": 0}
        return self.ticks.pop(0)

    async def close(self) -> None:
        self.connected = False


class LiveRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_runner_executes_and_shuts_down_gracefully(self):
        contract = Contract("CRUDEOIL", "MCX", 100, D("1"))
        broker = PaperBroker(slippage_bps=D(0))
        limits = RiskLimits(position_cap=500, max_daily_loss=D(10000), atr_spacing_multiple=D(1), pyramid_limit=3)

        # Series of prices designed to anchor grid at 6000 and trigger BUY at 5950 (ATR = 50)
        ticks = [
            {"last_price": 6000, "high": 6010, "low": 5990, "timestamp": 1},
            {"last_price": 5940, "high": 5950, "low": 5930, "timestamp": 2},
        ]
        stream = MockTickStream(ticks)

        runner = LiveRunner(
            contract=contract,
            tick_stream=stream,
            broker=broker,
            limits=limits,
            initial_atr=D(50),
        )

        runner_task = asyncio.create_task(runner.run())

        # Give enough time for ticks to be processed
        for _ in range(20):
            await asyncio.sleep(0.05)
            if broker.orders():
                break

        runner.stop()
        await asyncio.wait_for(runner_task, timeout=2.0)

        # Check that at least one order was placed and filled
        orders = broker.orders()
        self.assertGreater(len(orders), 0)
        fill = orders[0]
        self.assertEqual(fill.contract.symbol, "CRUDEOIL")
        self.assertEqual(fill.quantity, 100)
        self.assertEqual(fill.side, "BUY")
