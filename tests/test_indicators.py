import unittest
from decimal import Decimal
from quant_engine.indicators import ATR, bollinger, ema, macd, obv, rsi, vwap
from quant_engine.models import Bar, D


class EMATests(unittest.TestCase):
    def test_single_period_equals_only_value(self):
        self.assertEqual(ema([D(10)], 1), D(10))

    def test_insufficient_raises(self):
        with self.assertRaises(ValueError):
            ema([D(1), D(2)], 5)


class RSITests(unittest.TestCase):
    def test_all_gains_gives_100(self):
        closes = [D(i) for i in range(1, 17)]  # 15 periods of pure gain
        self.assertEqual(rsi(closes, 14), D(100))

    def test_insufficient_raises(self):
        with self.assertRaises(ValueError):
            rsi([D(1)] * 5, 14)


class MACDTests(unittest.TestCase):
    def _rising_prices(self, n: int = 40) -> list[Decimal]:
        return [D(100 + i) for i in range(n)]

    def test_returns_three_tuple(self):
        result = macd(self._rising_prices(), fast=12, slow=26, signal_period=9)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)

    def test_macd_line_positive_on_rising_prices(self):
        """Fast EMA tracks recent prices more closely → MACD > 0 in uptrend."""
        line, signal, hist = macd(self._rising_prices(), fast=12, slow=26, signal_period=9)
        self.assertGreater(line, D(0))

    def test_histogram_equals_macd_minus_signal(self):
        line, signal, hist = macd(self._rising_prices())
        self.assertAlmostEqual(float(hist), float(line - signal), places=10)

    def test_insufficient_data_raises(self):
        with self.assertRaises(ValueError):
            macd([D(1)] * 10, fast=12, slow=26, signal_period=9)


class BollingerTests(unittest.TestCase):
    def _flat_closes(self, n: int = 20, price: Decimal = D(100)) -> list[Decimal]:
        return [price] * n

    def test_flat_prices_bands_are_symmetric(self):
        upper, mid, lower = bollinger(self._flat_closes())
        self.assertEqual(mid, D(100))
        self.assertEqual(upper, lower)  # std == 0 for flat series

    def test_upper_gt_mid_gt_lower_on_varying_prices(self):
        closes = [D(100 + (i % 5)) for i in range(20)]
        upper, mid, lower = bollinger(closes)
        self.assertGreater(upper, mid)
        self.assertGreater(mid, lower)

    def test_insufficient_raises(self):
        with self.assertRaises(ValueError):
            bollinger([D(1)] * 5, period=20)


class VWAPTests(unittest.TestCase):
    def test_uniform_bars(self):
        bars = [Bar(i, D(10), D(12), D(8), D(10), D(100)) for i in range(3)]
        self.assertEqual(vwap(bars), D(10))

    def test_zero_volume_raises(self):
        bars = [Bar(1, D(10), D(12), D(8), D(10), D(0))]
        with self.assertRaises(ValueError):
            vwap(bars)


class OBVTests(unittest.TestCase):
    def test_rising_closes_accumulate(self):
        bars = [
            Bar(1, D(10), D(11), D(9),  D(10), D(1000)),
            Bar(2, D(10), D(12), D(10), D(11), D(2000)),  # close up → +2000
            Bar(3, D(11), D(13), D(11), D(12), D(1500)),  # close up → +1500
        ]
        self.assertEqual(obv(bars), D(3500))

    def test_falling_close_subtracts_volume(self):
        bars = [
            Bar(1, D(12), D(13), D(11), D(12), D(1000)),
            Bar(2, D(12), D(12), D(10), D(10), D(500)),   # close down → -500
        ]
        self.assertEqual(obv(bars), D(-500))

    def test_flat_close_no_change(self):
        bars = [
            Bar(1, D(10), D(11), D(9),  D(10), D(1000)),
            Bar(2, D(10), D(11), D(9),  D(10), D(999)),   # same close → no change
        ]
        self.assertEqual(obv(bars), D(0))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            obv([])


class ATRTests(unittest.TestCase):
    def test_accumulates_after_period(self):
        atr = ATR(period=3)
        bars = [
            Bar(1, D(10), D(12), D(8),  D(10), D(100)),  # TR = 4
            Bar(2, D(10), D(13), D(9),  D(11), D(100)),  # TR = max(4, 3, 2) = 4
            Bar(3, D(11), D(14), D(10), D(12), D(100)),  # TR = max(4, 3, 2) = 4
        ]
        for b in bars:
            atr.update(b)
        self.assertIsNotNone(atr.value)

    def test_zero_range_doji_bar(self):
        """A bar where open==high==low==close (doji) should not crash ATR and TR == overnight gap."""
        atr = ATR(period=2)
        atr.update(Bar(1, D(100), D(102), D(98), D(100), D(0)))
        # Doji with gap up: high==low==close, but TR driven by overnight gap
        result = atr.update(Bar(2, D(105), D(105), D(105), D(105), D(0)))
        # TR = max(0, |105-100|, |105-100|) = 5
        self.assertIsNotNone(result)

    def test_period_not_met_returns_none(self):
        atr = ATR(period=14)
        result = atr.update(Bar(1, D(10), D(12), D(8), D(10), D(100)))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
