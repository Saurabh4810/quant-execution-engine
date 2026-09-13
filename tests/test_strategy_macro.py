import unittest
from quant_engine.macro import MacroRegimeEngine, Regime
from quant_engine.models import D, Position
from quant_engine.strategy import GridEngine, RiskGate, RiskLimits, StopAndReverseEngine


class StrategyMacroTests(unittest.TestCase):
    def setUp(self):
        self.limits = RiskLimits(100, D(500), D(1), 2)

    def test_grid_uses_atr_spacing(self):
        grid, pos = GridEngine(self.limits), Position()
        self.assertIsNone(grid.signal(D(100), D(5), pos))
        self.assertEqual(grid.signal(D(95), D(5), pos), "BUY")

    def test_grid_pyramid_limit_respected(self):
        grid, pos = GridEngine(self.limits), Position()
        # Anchor at 100
        self.assertIsNone(grid.signal(D(100), D(5), pos))
        # Level 1 (anchor becomes 95)
        self.assertEqual(grid.signal(D(95), D(5), pos), "BUY")
        # Level 2 (anchor becomes 90)
        self.assertEqual(grid.signal(D(90), D(5), pos), "BUY")
        # Level 3: pyramid limit is 2, so this should return None
        self.assertIsNone(grid.signal(D(85), D(5), pos))

    def test_kill_switch_latches(self):
        gate = RiskGate(self.limits)
        self.assertFalse(gate.permit(Position(), "BUY", 10, D(-500)))
        self.assertFalse(gate.permit(Position(), "BUY", 10, D(0)))

    def test_position_cap(self):
        self.assertFalse(RiskGate(self.limits).permit(Position(100), "BUY", 1, D(0)))

    def test_stop_and_reverse_long(self):
        # Long 10 @ 100, stop is 2 * 2 = 4, price falls to 95 <= 96 -> SELL
        self.assertEqual(StopAndReverseEngine(D(2)).signal(D(95), D(2), Position(10, D(100))), "SELL")

    def test_stop_and_reverse_short(self):
        # Short 10 @ 100, stop is 2 * 2 = 4, price rises to 105 >= 104 -> BUY
        self.assertEqual(StopAndReverseEngine(D(2)).signal(D(105), D(2), Position(-10, D(100))), "BUY")

    def test_stop_and_reverse_no_trigger_within_bounds(self):
        sar = StopAndReverseEngine(D(2))
        self.assertIsNone(sar.signal(D(99), D(2), Position(10, D(100))))
        self.assertIsNone(sar.signal(D(101), D(2), Position(-10, D(100))))
        self.assertIsNone(sar.signal(D(100), D(2), Position(0, D(100))))

    def test_macro_risk_off_disables_trading(self):
        outcome = MacroRegimeEngine().decide({"vix": D("-0.8"), "credit": D("-0.6")})
        self.assertEqual(outcome.regime, Regime.RISK_OFF)
        self.assertFalse(outcome.trading_enabled)

    def test_macro_risk_on_and_neutral(self):
        engine = MacroRegimeEngine()
        risk_on = engine.decide({"vix": D("0.8"), "yield_curve": D("0.6")})
        self.assertEqual(risk_on.regime, Regime.RISK_ON)
        self.assertTrue(risk_on.trading_enabled)
        self.assertEqual(risk_on.spacing_multiplier, D("0.80"))

        neutral = engine.decide({"vix": D("0.0"), "credit": D("0.0")})
        self.assertEqual(neutral.regime, Regime.NEUTRAL)
        self.assertTrue(neutral.trading_enabled)
        self.assertEqual(neutral.spacing_multiplier, D("1.00"))
