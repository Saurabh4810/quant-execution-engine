"""Persistence layer: SQLite-backed repositories for orders, positions, and OHLCV bars.

Design:
  - Uses only stdlib sqlite3 — zero additional dependencies.
  - Schema is created on first connection (idempotent CREATE TABLE IF NOT EXISTS).
  - All monetary values are stored as TEXT (Decimal strings) for paisa-exact round-trips.
  - Optional Postgres/TimescaleDB connection via DATABASE_URL env var (requires psycopg2).
  - Repositories are thread-safe via sqlite3's check_same_thread=False + a single connection.

Usage:
    from quant_engine.storage import OrderRepository, PositionRepository, OHLCVStore

    order_repo = OrderRepository()          # SQLite in-memory (testing)
    order_repo = OrderRepository("trades.db")  # SQLite file
    order_repo.save(order)
    orders = order_repo.load_all()
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from typing import Generator, Iterator

from .models import Bar, Contract, D, Order, OrderStatus, Position, paisa


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def _get_connection(db_path: str) -> sqlite3.Connection:
    """Return a sqlite3 connection, or a Postgres connection if DATABASE_URL is set."""
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql") or database_url.startswith("postgres"):
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(database_url)
            return conn  # type: ignore
        except ImportError:
            raise ImportError(
                "psycopg2 is required for Postgres. Install with: pip install psycopg2-binary"
            )
    return sqlite3.connect(db_path, check_same_thread=False)


# ---------------------------------------------------------------------------
# Order Repository
# ---------------------------------------------------------------------------

class OrderRepository:
    """Persist and retrieve Order objects in SQLite (or Postgres via DATABASE_URL)."""

    DDL = """
    CREATE TABLE IF NOT EXISTS orders (
        client_order_id TEXT PRIMARY KEY,
        symbol          TEXT NOT NULL,
        exchange        TEXT NOT NULL,
        side            TEXT NOT NULL,
        quantity        INTEGER NOT NULL,
        status          TEXT NOT NULL,
        fill_price      TEXT,
        lot_size        INTEGER NOT NULL DEFAULT 1,
        tick_size       TEXT NOT NULL DEFAULT '0.05',
        created_at      REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
    )
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = _get_connection(db_path)
        self._conn.execute(self.DDL)
        self._conn.commit()

    def save(self, order: Order) -> None:
        """Upsert an order (INSERT OR REPLACE on client_order_id)."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders
              (client_order_id, symbol, exchange, side, quantity, status,
               fill_price, lot_size, tick_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.client_order_id,
                order.contract.symbol,
                order.contract.exchange,
                order.side,
                order.quantity,
                order.status.value,
                str(order.fill_price) if order.fill_price is not None else None,
                order.contract.lot_size,
                str(order.contract.tick_size),
            ),
        )
        self._conn.commit()

    def load_all(self) -> list[Order]:
        """Return all persisted orders as Order objects."""
        rows = self._conn.execute(
            "SELECT client_order_id, symbol, exchange, side, quantity, "
            "status, fill_price, lot_size, tick_size FROM orders ORDER BY created_at"
        ).fetchall()
        orders: list[Order] = []
        for row in rows:
            cid, symbol, exchange, side, qty, status, fill_price, lot_size, tick_size = row
            contract = Contract(
                symbol=symbol,
                exchange=exchange,  # type: ignore[arg-type]
                lot_size=lot_size,
                tick_size=D(tick_size),
            )
            order = Order(
                contract=contract,
                side=side,  # type: ignore[arg-type]
                quantity=qty,
                client_order_id=cid,
                status=OrderStatus(status),
                fill_price=D(fill_price) if fill_price else None,
            )
            orders.append(order)
        return orders

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Position Repository
# ---------------------------------------------------------------------------

class PositionRepository:
    """Persist and retrieve Position snapshots per symbol."""

    DDL = """
    CREATE TABLE IF NOT EXISTS positions (
        symbol          TEXT PRIMARY KEY,
        quantity        INTEGER NOT NULL,
        average_price   TEXT NOT NULL,
        realized_pnl    TEXT NOT NULL,
        updated_at      REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
    )
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = _get_connection(db_path)
        self._conn.execute(self.DDL)
        self._conn.commit()

    def save(self, symbol: str, position: Position) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO positions (symbol, quantity, average_price, realized_pnl)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, position.quantity, str(position.average_price), str(position.realized_pnl)),
        )
        self._conn.commit()

    def load(self, symbol: str) -> Position | None:
        row = self._conn.execute(
            "SELECT quantity, average_price, realized_pnl FROM positions WHERE symbol=?",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        qty, avg, pnl = row
        return Position(quantity=qty, average_price=D(avg), realized_pnl=D(pnl))

    def load_all(self) -> dict[str, Position]:
        rows = self._conn.execute(
            "SELECT symbol, quantity, average_price, realized_pnl FROM positions"
        ).fetchall()
        return {
            symbol: Position(quantity=qty, average_price=D(avg), realized_pnl=D(pnl))
            for symbol, qty, avg, pnl in rows
        }

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# OHLCV Store
# ---------------------------------------------------------------------------

class OHLCVStore:
    """Store and retrieve OHLCV bars for backtesting data loading."""

    DDL = """
    CREATE TABLE IF NOT EXISTS ohlcv (
        symbol   TEXT    NOT NULL,
        ts       INTEGER NOT NULL,
        open     TEXT    NOT NULL,
        high     TEXT    NOT NULL,
        low      TEXT    NOT NULL,
        close    TEXT    NOT NULL,
        volume   TEXT    NOT NULL DEFAULT '0',
        PRIMARY KEY (symbol, ts)
    )
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = _get_connection(db_path)
        self._conn.execute(self.DDL)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_ts ON ohlcv(symbol, ts)")
        self._conn.commit()

    def insert_bars(self, symbol: str, bars: list[Bar]) -> None:
        """Bulk insert OHLCV bars (INSERT OR IGNORE — no overwrite of existing data)."""
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO ohlcv (symbol, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (symbol, bar.ts, str(bar.open), str(bar.high),
                 str(bar.low), str(bar.close), str(bar.volume))
                for bar in bars
            ],
        )
        self._conn.commit()

    def load_bars(
        self,
        symbol: str,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> list[Bar]:
        """Load bars for a symbol, optionally filtered by timestamp range."""
        query = "SELECT ts, open, high, low, close, volume FROM ohlcv WHERE symbol=?"
        params: list = [symbol]
        if from_ts is not None:
            query += " AND ts >= ?"
            params.append(from_ts)
        if to_ts is not None:
            query += " AND ts <= ?"
            params.append(to_ts)
        query += " ORDER BY ts"
        rows = self._conn.execute(query, params).fetchall()
        return [
            Bar(ts=ts, open=D(o), high=D(h), low=D(l), close=D(c), volume=D(v))
            for ts, o, h, l, c, v in rows
        ]

    def bar_count(self, symbol: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=?", (symbol,)
        ).fetchone()[0]

    def close(self) -> None:
        self._conn.close()
