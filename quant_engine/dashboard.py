"""Internal FastAPI dashboard: positions, fills, regime, and health endpoints.

Usage:
    uvicorn quant_engine.dashboard:app --host 0.0.0.0 --port 8000

Designed to be consumed by Streamlit or Grafana via HTTP scrape.
Requires: pip install fastapi uvicorn

No authentication is implemented here; add an API key middleware or place
behind a VPN/firewall before exposing to the network.
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse, PlainTextResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from .blotter import Blotter
from .execution import StateStore
from .macro import MacroRegimeEngine, Regime
from .models import D

# ---------------------------------------------------------------------------
# Shared state — injected at startup via configure()
# ---------------------------------------------------------------------------

_state: StateStore | None = None
_blotter: Blotter | None = None
_regime_engine: MacroRegimeEngine | None = None
_macro_proxies: dict[str, Decimal] = {}
_start_time = time.time()


def configure(
    state: StateStore,
    blotter: Blotter,
    regime_engine: MacroRegimeEngine | None = None,
    macro_proxies: dict[str, Decimal] | None = None,
) -> None:
    """Wire live objects into the dashboard before serving requests."""
    global _state, _blotter, _regime_engine, _macro_proxies
    _state = state
    _blotter = blotter
    _regime_engine = regime_engine or MacroRegimeEngine()
    _macro_proxies = macro_proxies or {}


def _decimal_default(obj: Any) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

if _FASTAPI_AVAILABLE:
    app = FastAPI(
        title="Quant Engine Dashboard",
        description="Internal observability API for positions, fills, regime, and health.",
        version="0.1.0",
    )

    @app.get("/health", summary="Liveness probe")
    def health() -> dict:
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "fastapi": True,
        }

    @app.get("/positions", summary="Current open positions")
    def positions() -> JSONResponse:
        if _state is None:
            raise HTTPException(503, "Engine state not configured")
        data = {
            symbol: {
                "quantity": pos.quantity,
                "average_price": str(pos.average_price),
                "realized_pnl": str(pos.realized_pnl),
            }
            for symbol, pos in _state.positions.items()
        }
        return JSONResponse(data)

    @app.get("/fills", summary="Blotter fill log")
    def fills(format: str = "json") -> Any:
        if _blotter is None:
            raise HTTPException(503, "Blotter not configured")
        if format == "csv":
            return PlainTextResponse(_blotter.to_csv(), media_type="text/csv")
        entries = [
            {
                "ts": e.ts,
                "symbol": e.symbol,
                "exchange": e.exchange,
                "side": e.side,
                "quantity": e.quantity,
                "fill_price": str(e.fill_price),
                "cost": str(e.cost),
                "client_order_id": e.client_order_id,
                "strategy_tag": e.strategy_tag,
            }
            for e in _blotter.entries()
        ]
        return JSONResponse({
            "fills": entries,
            "total_fills": len(entries),
            "total_cost": str(_blotter.total_realized_cost()),
        })

    @app.get("/regime", summary="Current macro regime decision")
    def regime() -> JSONResponse:
        if _regime_engine is None:
            raise HTTPException(503, "Regime engine not configured")
        decision = _regime_engine.decide(_macro_proxies)
        return JSONResponse({
            "regime": decision.regime,
            "score": str(decision.score),
            "spacing_multiplier": str(decision.spacing_multiplier),
            "trading_enabled": decision.trading_enabled,
            "proxies": {k: str(v) for k, v in _macro_proxies.items()},
        })

    @app.get("/orders", summary="All submitted orders (client IDs)")
    def orders() -> JSONResponse:
        if _state is None:
            raise HTTPException(503, "Engine state not configured")
        data = {
            cid: {"status": o.status, "side": o.side, "quantity": o.quantity,
                  "fill_price": str(o.fill_price) if o.fill_price else None}
            for cid, o in _state.orders.items()
        }
        return JSONResponse(data)

else:
    # Graceful stub when FastAPI is not installed
    class _StubApp:  # type: ignore
        def get(self, *a, **kw):
            def decorator(fn):
                return fn
            return decorator

    app = _StubApp()  # type: ignore
    print("WARNING: FastAPI not installed. Install with: pip install fastapi uvicorn")
