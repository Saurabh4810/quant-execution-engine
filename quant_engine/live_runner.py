"""Live execution runtime: wires tick stream → macro → strategy → risk → broker.

Design invariants:
  - All market data flows through a bounded asyncio.Queue (back-pressure).
  - A single stop_event controls graceful shutdown; the queue drains before exit.
  - Reconnect-resync calls broker.reconcile() after every WebSocket reconnect.
  - Broker credentials must come from environment variables; never hardcode.
  - Paper mode (PaperBroker) must be confirmed before switching to KiteBroker.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from .broker import Broker
from .execution import OrderService, StateStore
from .indicators import ATR
from .macro import MacroRegimeEngine
from .market_data import TickStream, RetryPolicy, with_retry
from .models import Bar, Contract, D
from .runtime import log_event
from .alerts import AlertChannel, LoggingAlertChannel
from .strategy import GridEngine, RiskGate, RiskLimits, StopAndReverseEngine

logger = logging.getLogger("quant_engine.live_runner")


class LiveRunner:
    """Async live trading loop.

    Args:
        contract:       Instrument to trade.
        tick_stream:    Live or simulated WebSocket tick source.
        broker:         Broker adapter (PaperBroker for paper mode).
        limits:         Risk limits — position cap, max daily loss, ATR multiple.
        macro_proxies:  Callable returning the current macro proxy dict.
        queue_maxsize:  Back-pressure limit; producers block when queue is full.
        atr_period:     ATR period for grid/SAR spacing.
        initial_atr:    Optional seed ATR for warm start before period bars accumulate.
        alert_channel:  Where to send deviation and kill-switch alerts.
        retry_policy:   Reconnect retry policy for the tick stream.
    """

    def __init__(
        self,
        contract: Contract,
        tick_stream: TickStream,
        broker: Broker,
        limits: RiskLimits,
        macro_proxies: dict[str, Decimal] | None = None,
        queue_maxsize: int = 512,
        atr_period: int = 14,
        initial_atr: Decimal | None = None,
        alert_channel: AlertChannel | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
    ):
        self.contract = contract
        self.tick_stream = tick_stream
        self.broker = broker
        self.limits = limits
        self.macro_proxies = macro_proxies or {}
        self.queue_maxsize = queue_maxsize
        self.atr_period = atr_period
        self.initial_atr = initial_atr
        self.alert: AlertChannel = alert_channel or LoggingAlertChannel()
        self.retry_policy = retry_policy

        # Stateful components — reset on each run() call
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._stop = asyncio.Event()
        self._order_service: OrderService | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the live trading loop.  Awaits until stop() is called."""
        state = StateStore()
        self._order_service = OrderService(self.broker, state)
        self._stop.clear()
        self._producer_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None

        log_event("live_runner_start", contract=self.contract.symbol,
                  exchange=self.contract.exchange)

        self._producer_task = asyncio.create_task(self._produce(), name="tick_producer")
        self._consumer_task = asyncio.create_task(self._consume(state), name="tick_consumer")
        try:
            try:
                await self._producer_task
            except asyncio.CancelledError:
                pass
            await self._consumer_task
        finally:
            if self._producer_task and not self._producer_task.done():
                self._producer_task.cancel()
            if self._consumer_task and not self._consumer_task.done():
                self._consumer_task.cancel()
            log_event("live_runner_stop", contract=self.contract.symbol)

    def stop(self) -> None:
        """Signal graceful shutdown.  The queue drains before the loop exits."""
        self._stop.set()
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()

    # ------------------------------------------------------------------
    # Internal: producer
    # ------------------------------------------------------------------

    async def _produce(self) -> None:
        """Read ticks from the stream and enqueue them.

        On disconnect, waits according to RetryPolicy, reconciles broker state,
        then reconnects.  Producer blocks when queue is full (back-pressure).
        """
        attempt = 0
        while not self._stop.is_set():
            try:
                await self.tick_stream.connect([])  # tokens injected via tick_stream config
                attempt = 0  # reset on successful connect
                await self._reconcile_after_reconnect()
                while not self._stop.is_set():
                    tick = await self.tick_stream.next_tick()
                    # Block if queue is full — explicit back-pressure
                    await self._queue.put(tick)
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt >= self.retry_policy.attempts:
                    await self.alert.notify("CRITICAL", "tick_stream_failed_permanently",
                                            error=str(exc), contract=self.contract.symbol)
                    self._stop.set()
                    return
                delay = self.retry_policy.base_delay_seconds * (2 ** attempt)
                log_event("tick_stream_reconnect", attempt=attempt, delay=delay, error=str(exc))
                await asyncio.sleep(delay)
                attempt += 1
            finally:
                try:
                    await self.tick_stream.close()
                except Exception:
                    pass

    async def _reconcile_after_reconnect(self) -> None:
        """After every reconnect, sync local order state with broker truth."""
        if self._order_service is not None:
            self._order_service.reconcile()
            log_event("broker_reconciled", contract=self.contract.symbol)

    # ------------------------------------------------------------------
    # Internal: consumer
    # ------------------------------------------------------------------

    async def _consume(self, state: StateStore) -> None:
        """Dequeue ticks and run the full signal → risk → order pipeline."""
        macro_engine = MacroRegimeEngine()
        grid = GridEngine(self.limits)
        sar = StopAndReverseEngine()
        risk = RiskGate(self.limits)
        atr_indicator = ATR(self.atr_period)
        daily_pnl = D(0)

        while not self._stop.is_set() or not self._queue.empty():
            try:
                tick: dict = await asyncio.wait_for(self._queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            try:
                await self._handle_tick(tick, state, macro_engine, grid, sar, risk,
                                        atr_indicator, daily_pnl)
            except Exception as exc:
                await self.alert.notify("ERROR", "tick_handler_exception",
                                        error=str(exc), contract=self.contract.symbol)
                log_event("tick_handler_error", error=str(exc))
            finally:
                self._queue.task_done()

    async def _handle_tick(
        self, tick: dict, state: StateStore,
        macro_engine: MacroRegimeEngine, grid: GridEngine,
        sar: StopAndReverseEngine, risk: RiskGate,
        atr_indicator: ATR, daily_pnl: Decimal,
    ) -> None:
        """Core signal pipeline for a single tick."""
        price = D(str(tick.get("last_price", 0)))
        if price <= 0:
            return

        # Macro regime check — circuit breaker
        regime = macro_engine.decide(self.macro_proxies)
        if not regime.trading_enabled:
            log_event("trading_disabled_by_macro", regime=regime.regime,
                      score=str(regime.score))
            return

        pos = state.positions.get(self.contract.symbol)
        from .models import Position
        if pos is None:
            pos = Position()

        high = D(str(tick.get("high", price)))
        low = D(str(tick.get("low", price)))
        ts = int(tick.get("timestamp", 0) or 0)
        atr_indicator.update(Bar(ts=ts, open=price, high=high, low=low, close=price))
        atr_val = atr_indicator.value or self.initial_atr
        if atr_val is None or atr_val <= 0:
            return  # not enough bars yet and no seed ATR

        # Apply macro spacing override
        effective_limits = self.limits.__class__(
            position_cap=self.limits.position_cap,
            max_daily_loss=self.limits.max_daily_loss,
            atr_spacing_multiple=self.limits.atr_spacing_multiple * regime.spacing_multiplier,
            pyramid_limit=self.limits.pyramid_limit,
        )
        grid.limits = effective_limits

        # Generate signals
        grid_signal = grid.signal(price, atr_val, pos)
        sar_signal = sar.signal(price, atr_val, pos)
        # SAR takes priority (stop protection)
        side = sar_signal or grid_signal
        if side is None:
            return

        # Risk gate
        if not risk.permit(pos, side, self.contract.lot_size, daily_pnl):
            await self.alert.notify("WARNING", "risk_gate_blocked", side=side,
                                    price=str(price), contract=self.contract.symbol)
            return

        # Submit order — idempotency key = price + side + ts
        client_id = f"{self.contract.symbol}:{side}:{tick.get('timestamp', price)}"
        order = self._order_service.submit(
            self.contract, side, self.contract.lot_size, price, client_id
        )
        log_event("order_submitted", client_id=client_id, side=side,
                  price=str(price), status=order.status)
