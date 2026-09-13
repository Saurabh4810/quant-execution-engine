# Quant Execution Engine

Production-grade, paper-trading-first algorithmic execution engine developed for the Quant Developer role. Built dependency-free in core logic (using Python stdlib with optional FastAPI/SQLite adapters), featuring exact paisa precision, zero lookahead bias, Indian market plumbing (MCX and NSE F&O), live runtime with graceful shutdown, SDLC automation, and automated desk reconciliation.

## Architecture

```text
market ticks / WS ──> bounded queue ──> macro regime ──> strategy engines ──> risk gate ──> order service ──> broker
       │                     │                 │                   │               │                │
       └── contract master   └── back-pressure └── proxy scoring   └── Grid / SAR  └── kill switch  └── idempotency
                                                                                                            │
backtest bars ──────────────────────────────────────────────────────────────────────────────────────────────┴──> blotter / DB
```

### Modules

| Module | Responsibility |
|---|---|
| [`models`](file:///quant_engine/models.py) | Paisa-exact Decimal arithmetic, Indian exchange charges (STT/CTT/GST), `ContractSpec` presets |
| [`indicators`](file:///quant_engine/indicators.py) | Incremental Wilder ATR, EMA, RSI, MACD, Bollinger Bands, VWAP, and OBV (no lookahead) |
| [`strategy`](file:///quant_engine/strategy.py) | ATR-spaced grid engine, stop-and-reverse engine (SAR), `RiskGate` with latched kill switch |
| [`macro`](file:///quant_engine/macro.py) | Multi-factor proxy scoring (`RISK_ON`, `NEUTRAL`, `RISK_OFF`), regime multiplier, circuit breaker |
| [`market_plumbing`](file:///quant_engine/market_plumbing.py) | MCX contract tables (`CRUDEOIL`, `GOLD`, `SILVER`, etc.), NSE F&O SPAN margin stub, rollover calendar |
| [`broker`](file:///quant_engine/broker.py) | `PaperBroker` (slippage simulation) and `KiteBroker` (Zerodha REST with token refresh & 429 backoff) |
| [`kite_ws`](file:///quant_engine/kite_ws.py) | Kite Connect binary WebSocket tick stream consumer with auto-reconnect |
| [`execution`](file:///quant_engine/execution.py) | Idempotent order placement (`submit()`) and state reconciliation after restart |
| [`live_runner`](file:///quant_engine/live_runner.py) | Async live loop wiring tick stream → macro → strategy → risk → broker with graceful shutdown |
| [`backtest`](file:///quant_engine/backtest.py) | Next-bar fills, slippage/tax/brokerage accounting, `WalkForwardBacktest`, and desk reconciliation |
| [`blotter`](file:///quant_engine/blotter.py) | Append-only fill log, CSV & JSON blotters, and execution deviation detector |
| [`alerts`](file:///quant_engine/alerts.py) | Alert channels: `LoggingAlertChannel`, `TwilioAlertChannel` (phone/SMS), `WebhookAlertChannel` (Slack/Teams) |
| [`dashboard`](file:///quant_engine/dashboard.py) | FastAPI observability endpoints (`/positions`, `/fills`, `/regime`, `/health`) |
| [`storage`](file:///quant_engine/storage.py) | SQLite-backed repositories for orders, positions, and OHLCV bar store |
| [`sdlc_agent`](file:///quant_engine/sdlc_agent.py) | Automated regression test agent comparing current backtest against locked baseline |

---

## Commands & SDLC

```bash
# Run the full test suite (68 unit & integration tests)
make test

# Run sample MCX Crude Oil backtest
make backtest

# Run SDLC regression agent (baseline metric comparison)
make sdlc-check

# Run linting & type checks
make lint
make typecheck
```

