from __future__ import annotations

from collections import deque
from decimal import Decimal, ROUND_HALF_UP
from .models import Bar, D


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

def ema(values: list[Decimal], period: int) -> Decimal:
    """Exponential Moving Average over a flat list of Decimal prices."""
    if len(values) < period:
        raise ValueError("insufficient values")
    result = sum(values[:period], D(0)) / period
    alpha = D(2) / D(period + 1)
    for value in values[period:]:
        result = alpha * value + (D(1) - alpha) * result
    return result


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    """Wilder RSI.  Returns a value in [0, 100]."""
    if len(closes) < period + 1:
        raise ValueError("insufficient closes")
    changes = [closes[i] - closes[i - 1] for i in range(1, period + 1)]
    gains = sum((max(x, D(0)) for x in changes), D(0)) / period
    losses = sum((max(-x, D(0)) for x in changes), D(0)) / period
    return D(100) if losses == 0 else D(100) - D(100) / (D(1) + gains / losses)


def macd(
    closes: list[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[Decimal, Decimal, Decimal]:
    """MACD line, signal line, and histogram — all Decimal-clean.

    Returns (macd_line, signal_line, histogram).
    Requires at least ``slow + signal_period - 1`` data points.
    """
    required = slow + signal_period - 1
    if len(closes) < required:
        raise ValueError(f"macd requires at least {required} closes, got {len(closes)}")

    # Build a series of (EMA_fast - EMA_slow) values over a rolling window
    macd_series: list[Decimal] = []
    for end in range(slow, len(closes) + 1):
        window = closes[:end]
        macd_series.append(ema(window, fast) - ema(window, slow))

    macd_line = macd_series[-1]
    signal_line = ema(macd_series, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

def bollinger(
    closes: list[Decimal],
    period: int = 20,
    num_std: Decimal = D(2),
) -> tuple[Decimal, Decimal, Decimal]:
    """Bollinger Bands: (upper, mid, lower).  Mid is a simple moving average."""
    if len(closes) < period:
        raise ValueError(f"bollinger requires at least {period} closes")
    window = closes[-period:]
    mid = sum(window, D(0)) / period
    variance = sum(((p - mid) ** 2 for p in window), D(0)) / D(period)
    # Integer sqrt via Newton's method on Decimal to avoid float conversion
    std = variance.sqrt()
    band = num_std * std
    return mid + band, mid, mid - band


class ATR:
    """Wilder ATR. update() only uses the current completed bar; no future data."""

    def __init__(self, period: int = 14):
        self.period = period
        self._trs: deque[Decimal] = deque(maxlen=period)
        self._prev_close: Decimal | None = None
        self.value: Decimal | None = None

    def update(self, bar: Bar) -> Decimal | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close
        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs, D(0)) / self.period
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def vwap(bars: list[Bar]) -> Decimal:
    """Session VWAP from a list of completed bars."""
    volume = sum((b.volume for b in bars), D(0))
    if volume == 0:
        raise ValueError("zero volume")
    return sum((((b.high + b.low + b.close) / 3) * b.volume for b in bars), D(0)) / volume


def obv(bars: list[Bar]) -> Decimal:
    """On-Balance Volume — running signed volume total.

    OBV rises when close > previous close and falls when close < previous close.
    A flat close leaves OBV unchanged.  Returns the final OBV value.
    """
    if not bars:
        raise ValueError("obv requires at least one bar")
    running = D(0)
    prev_close: Decimal | None = None
    for bar in bars:
        if prev_close is None:
            prev_close = bar.close
            continue
        if bar.close > prev_close:
            running += bar.volume
        elif bar.close < prev_close:
            running -= bar.volume
        prev_close = bar.close
    return running
