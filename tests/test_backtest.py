import unittest
from quant_engine.backtest import Backtest, WalkForwardBacktest, reconcile_with_desk
from quant_engine.models import Bar, Contract, D, Position


def _bars(n: int, start: int = 100, step: int = 1) -> list[Bar]:
    return [Bar(i, D(start + i * step), D(start + i * step + 1),
                D(start + i * step - 1), D(start + i * step)) for i in range(n)]


CONTRACT = Contract("NSE:ABC", "NSE", 1, D("0.05"))


class BacktestTests(unittest.TestCase):
    def test_fills_next_bar_open_not_signal_close(self):
        c = CONTRACT
        bars = [Bar(1, D(100), D(101), D(99), D(100)),
                Bar(2, D(110), D(111), D(109), D(110)),
                Bar(3, D(120), D(121), D(119), D(120))]
        calls = []

        def signal(history, pos):
            calls.append(history[-1].close)
            return "BUY" if len(history) == 1 else "SELL"

        result = Backtest(c, D(0), D(0), D(0)).run(bars, signal)
        self.assertEqual(calls, [D(100), D(110)])
        self.assertEqual(result.realized_pnl, D("10.00"))
        self.assertEqual(result.fills, 2)

    def test_fill_records_populated(self):
        bars = _bars(3)
        result = Backtest(CONTRACT, D(0), D(0), D(0)).run(
            bars, lambda h, p: "BUY" if len(h) == 1 else "SELL"
        )
        self.assertEqual(len(result.fill_records), 2)
        self.assertEqual(result.fill_records[0].side, "BUY")

    def test_csv_round_trip(self):
        bars = _bars(3)
        result = Backtest(CONTRACT, D(0), D(0), D(0)).run(
            bars, lambda h, p: "BUY" if len(h) == 1 else "SELL"
        )
        csv_str = result.to_csv()
        self.assertIn("bar_index", csv_str)
        self.assertIn("BUY", csv_str)


class WalkForwardTests(unittest.TestCase):
    def test_oos_window_does_not_receive_is_bars(self):
        """Verify that out-of-sample bars seen by the signal never include in-sample bar timestamps."""
        bars = _bars(20)
        is_size, oos_size = 10, 5
        wf = WalkForwardBacktest(CONTRACT, is_size, oos_size, D(0), D(0), D(0))

        for w in wf._windows(len(bars)):
            seen_is: list[int] = []
            seen_oos: list[int] = []

            def make_signal(acc: list[int]):
                def signal(history, pos):
                    acc.append(history[-1].ts)
                    return None
                return signal

            is_bars = bars[w.in_sample_start:w.in_sample_end]
            oos_bars = bars[w.out_sample_start:w.out_sample_end]
            wf.engine.run(is_bars, make_signal(seen_is))
            wf.engine.run(oos_bars, make_signal(seen_oos))

            self.assertTrue(set(seen_is).isdisjoint(set(seen_oos)),
                            "IS and OOS timestamps overlap!")
            self.assertLessEqual(max(seen_is), min(seen_oos),
                                 "IS bars leaked into OOS window — lookahead detected!")

    def test_walk_forward_produces_windows(self):
        bars = _bars(30)
        wf = WalkForwardBacktest(CONTRACT, 10, 5, D(0), D(0), D(0))
        result = wf.run(bars, lambda: (lambda h, p: None))
        self.assertGreater(len(result.windows), 0)

    def test_invalid_window_size_raises(self):
        with self.assertRaises(ValueError):
            WalkForwardBacktest(CONTRACT, 0, 5)


class ReconciliationTests(unittest.TestCase):
    def test_clean_reconciliation_self_matches(self):
        bars = _bars(5)
        result = Backtest(CONTRACT, D(0), D(20), D(1)).run(
            bars, lambda h, p: "BUY" if len(h) == 1 else ("SELL" if len(h) == 3 else None)
        )
        csv_str = result.to_csv()
        report = reconcile_with_desk(result, csv_str)
        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_price_diff, D("0.00"))

    def test_desk_price_mismatch_detected(self):
        bars = _bars(3)
        result = Backtest(CONTRACT, D(0), D(0), D(0)).run(
            bars, lambda h, p: "BUY" if len(h) == 1 else "SELL"
        )
        # Corrupt the desk CSV by tweaking fill_price
        csv_str = result.to_csv()
        corrupted = csv_str.replace(str(result.fill_records[0].fill_price), "9999.00", 1)
        report = reconcile_with_desk(result, corrupted, tolerance=D("0.01"))
        self.assertFalse(report.is_clean)

    def test_fill_count_mismatch_raises(self):
        bars = _bars(3)
        result = Backtest(CONTRACT, D(0), D(0), D(0)).run(
            bars, lambda h, p: "BUY" if len(h) == 1 else "SELL"
        )
        with self.assertRaises(ValueError):
            reconcile_with_desk(result, "bar_index,side,quantity,fill_price,cost\n")


if __name__ == "__main__":
    unittest.main()
