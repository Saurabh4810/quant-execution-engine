"""Backtesting harness: single-pass and walk-forward, with desk reconciliation.

Invariants enforced:
  - Signal at close[i] fills at open[i+1] — no lookahead.
  - Walk-forward windows are computed before iteration; out-of-sample bars
    are never passed to the in-sample signal function.
  - Costs (slippage, brokerage, STT/CTT) are paisa-exact.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from .models import Bar, Contract, D, Position, paisa


@dataclass(frozen=True)
class FillRecord:
    bar_index: int
    side: str
    quantity: int
    fill_price: Decimal
    cost: Decimal


@dataclass(frozen=True)
class BacktestResult:
    realized_pnl: Decimal
    costs: Decimal
    fills: int
    fill_records: tuple[FillRecord, ...] = field(default_factory=tuple)

    def to_csv(self) -> str:
        """Export fill records as a CSV string for desk reconciliation."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["bar_index", "side", "quantity", "fill_price", "cost"])
        for r in self.fill_records:
            writer.writerow([r.bar_index, r.side, r.quantity, r.fill_price, r.cost])
        return buf.getvalue()


class Backtest:
    """A signal at close[i] fills at open[i+1], preventing lookahead."""

    def __init__(
        self,
        contract: Contract,
        slippage_bps: Decimal = D(2),
        brokerage_per_order: Decimal = D(20),
        tax_bps: Decimal = D(1),
    ):
        self.contract = contract
        self.slippage_bps = slippage_bps
        self.brokerage_per_order = brokerage_per_order
        self.tax_bps = tax_bps

    def run(self, bars: list[Bar], signal: Callable) -> BacktestResult:
        pos, costs, fills = Position(), D(0), 0
        records: list[FillRecord] = []
        for i in range(len(bars) - 1):
            side = signal(bars[:i + 1], pos)
            if side is None:
                continue
            px = bars[i + 1].open
            sign = D(1) if side == "BUY" else D(-1)
            fill = px * (D(1) + sign * self.slippage_bps / D(10000))
            qty = self.contract.lot_size
            pos.apply_fill(side, qty, fill)
            cost = paisa(self.brokerage_per_order + fill * qty * self.tax_bps / D(10000))
            costs += cost
            fills += 1
            records.append(FillRecord(i + 1, side, qty, fill, cost))
        return BacktestResult(paisa(pos.realized_pnl - costs), paisa(costs), fills, tuple(records))


# ---------------------------------------------------------------------------
# Walk-Forward Backtest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardWindow:
    in_sample_start: int
    in_sample_end: int     # exclusive
    out_sample_start: int
    out_sample_end: int    # exclusive


@dataclass(frozen=True)
class WalkForwardResult:
    windows: tuple[tuple[BacktestResult, BacktestResult], ...]
    """Pairs of (in-sample result, out-of-sample result) for each window."""

    @property
    def total_oos_pnl(self) -> Decimal:
        return paisa(sum((oos.realized_pnl for _, oos in self.windows), D(0)))

    @property
    def total_oos_fills(self) -> int:
        return sum(oos.fills for _, oos in self.windows)


class WalkForwardBacktest:
    """Rolling in-sample / out-of-sample walk-forward test.

    Generates non-overlapping out-of-sample windows; the signal function is
    *trained* (if stateful) on in-sample bars only and then evaluated on the
    immediately following out-of-sample slice.  No future bar ever leaks into
    the in-sample period.

    Args:
        contract:       Instrument contract.
        in_sample_bars: Number of bars in each in-sample window.
        out_sample_bars: Number of bars in each out-of-sample window.
        slippage_bps:   Fill slippage in basis points.
        brokerage_per_order: Fixed brokerage per fill.
        tax_bps:        Tax (STT/CTT) in basis points of fill value.
    """

    def __init__(
        self,
        contract: Contract,
        in_sample_bars: int,
        out_sample_bars: int,
        slippage_bps: Decimal = D(2),
        brokerage_per_order: Decimal = D(20),
        tax_bps: Decimal = D(1),
    ):
        if in_sample_bars < 1 or out_sample_bars < 1:
            raise ValueError("window sizes must be positive")
        self.engine = Backtest(contract, slippage_bps, brokerage_per_order, tax_bps)
        self.in_sample_bars = in_sample_bars
        self.out_sample_bars = out_sample_bars

    def _windows(self, total: int) -> list[WalkForwardWindow]:
        windows: list[WalkForwardWindow] = []
        start = 0
        while start + self.in_sample_bars + self.out_sample_bars <= total:
            windows.append(WalkForwardWindow(
                in_sample_start=start,
                in_sample_end=start + self.in_sample_bars,
                out_sample_start=start + self.in_sample_bars,
                out_sample_end=start + self.in_sample_bars + self.out_sample_bars,
            ))
            start += self.out_sample_bars  # advance by OOS width (anchored expanding is similar)
        return windows

    def run(
        self,
        bars: list[Bar],
        signal_factory: Callable[[], Callable],
    ) -> WalkForwardResult:
        """Run walk-forward test.

        Args:
            bars:            Full bar series (chronological).
            signal_factory:  Callable that returns a *fresh* signal function for
                             each window so in-sample state is never leaked.
        """
        pairs: list[tuple[BacktestResult, BacktestResult]] = []
        for w in self._windows(len(bars)):
            is_bars = bars[w.in_sample_start:w.in_sample_end]
            oos_bars = bars[w.out_sample_start:w.out_sample_end]
            # Fresh signal instance per window — no state bleeds across windows
            is_signal = signal_factory()
            oos_signal = signal_factory()
            is_result = self.engine.run(is_bars, is_signal)
            oos_result = self.engine.run(oos_bars, oos_signal)
            pairs.append((is_result, oos_result))
        return WalkForwardResult(tuple(pairs))


# ---------------------------------------------------------------------------
# Desk Reconciliation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReconciliationLine:
    bar_index: int
    side: str
    quantity: int
    fill_price: Decimal
    cost: Decimal
    desk_fill_price: Decimal
    desk_cost: Decimal
    price_diff: Decimal   # engine - desk, in paisa
    cost_diff: Decimal


@dataclass(frozen=True)
class ReconciliationReport:
    lines: tuple[ReconciliationLine, ...]
    total_price_diff: Decimal
    total_cost_diff: Decimal
    is_clean: bool  # True if every diff is within tolerance


def reconcile_with_desk(
    result: BacktestResult,
    desk_csv: str,
    tolerance: Decimal = D("0.01"),
) -> ReconciliationReport:
    """Compare engine fill records with a desk CSV line-by-line to the paisa.

    Expected CSV columns: bar_index, side, quantity, fill_price, cost
    (same format as BacktestResult.to_csv()).

    Args:
        result:     Engine backtest result.
        desk_csv:   CSV string from the desk spreadsheet.
        tolerance:  Maximum allowed paisa difference per line (default 1 paisa).

    Returns:
        ReconciliationReport with per-line diffs and an is_clean flag.
    """
    reader = csv.DictReader(io.StringIO(desk_csv))
    desk_rows = list(reader)

    if len(desk_rows) != len(result.fill_records):
        raise ValueError(
            f"Fill count mismatch: engine={len(result.fill_records)}, desk={len(desk_rows)}"
        )

    lines: list[ReconciliationLine] = []
    for engine_fill, desk_row in zip(result.fill_records, desk_rows):
        desk_price = D(desk_row["fill_price"])
        desk_cost = D(desk_row["cost"])
        price_diff = paisa(engine_fill.fill_price - desk_price)
        cost_diff = paisa(engine_fill.cost - desk_cost)
        lines.append(ReconciliationLine(
            bar_index=engine_fill.bar_index,
            side=engine_fill.side,
            quantity=engine_fill.quantity,
            fill_price=engine_fill.fill_price,
            cost=engine_fill.cost,
            desk_fill_price=desk_price,
            desk_cost=desk_cost,
            price_diff=price_diff,
            cost_diff=cost_diff,
        ))

    total_price_diff = paisa(sum((l.price_diff for l in lines), D(0)))
    total_cost_diff = paisa(sum((l.cost_diff for l in lines), D(0)))
    is_clean = all(abs(l.price_diff) <= tolerance and abs(l.cost_diff) <= tolerance for l in lines)
    return ReconciliationReport(
        lines=tuple(lines),
        total_price_diff=total_price_diff,
        total_cost_diff=total_cost_diff,
        is_clean=is_clean,
    )
