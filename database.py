"""Very small SQLite persistence layer.

Everything runs through a single connection guarded by a lock and is executed
in a worker thread via asyncio.to_thread so the async bot code never blocks.
"""
import sqlite3
import asyncio
import time
from contextlib import contextmanager
from typing import Optional, Any

import config

_lock = asyncio.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id     INTEGER PRIMARY KEY,
            username        TEXT,
            wallet_usd      REAL NOT NULL DEFAULT 0,
            is_banned       INTEGER NOT NULL DEFAULT 0,
            last_demo_credit_ts REAL,
            created_at      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id       INTEGER NOT NULL,
            product_id        INTEGER NOT NULL,
            product_name      TEXT NOT NULL,
            quantity          INTEGER NOT NULL,
            unit_price_usd    REAL NOT NULL,
            total_price_usd   REAL NOT NULL,
            status            TEXT NOT NULL,          -- processing / delivered / failed / cancelled
            accounts          TEXT,                    -- JSON-encoded list, once delivered
            irmarket_order_id INTEGER,
            idempotency_key   TEXT UNIQUE,
            created_at        REAL NOT NULL,
            updated_at        REAL NOT NULL,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


async def init_db() -> None:
    global _conn
    async with _lock:
        _conn = _connect()
        _init_schema(_conn)


def _require_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Database not initialised - call init_db() first")
    return _conn


# ---------------------------------------------------------------- users ----
async def get_or_create_user(telegram_id: int, username: Optional[str]) -> sqlite3.Row:
    async with _lock:
        conn = _require_conn()
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            if username and row["username"] != username:
                conn.execute(
                    "UPDATE users SET username = ? WHERE telegram_id = ?", (username, telegram_id)
                )
                conn.commit()
            return conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
        conn.execute(
            "INSERT INTO users (telegram_id, username, wallet_usd, created_at) VALUES (?, ?, 0, ?)",
            (telegram_id, username, time.time()),
        )
        conn.commit()
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


async def get_user(telegram_id: int) -> Optional[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


async def list_users(limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        return conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()


async def count_users() -> int:
    async with _lock:
        conn = _require_conn()
        return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


async def adjust_wallet(telegram_id: int, delta_usd: float) -> float:
    """Add (or subtract, if negative) delta_usd to a user's wallet. Returns new balance."""
    async with _lock:
        conn = _require_conn()
        conn.execute(
            "UPDATE users SET wallet_usd = wallet_usd + ? WHERE telegram_id = ?",
            (delta_usd, telegram_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT wallet_usd FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()["wallet_usd"]


async def mark_demo_credit_claimed(telegram_id: int) -> None:
    async with _lock:
        conn = _require_conn()
        conn.execute(
            "UPDATE users SET last_demo_credit_ts = ? WHERE telegram_id = ?",
            (time.time(), telegram_id),
        )
        conn.commit()


async def set_ban(telegram_id: int, banned: bool) -> None:
    async with _lock:
        conn = _require_conn()
        conn.execute(
            "UPDATE users SET is_banned = ? WHERE telegram_id = ?", (1 if banned else 0, telegram_id)
        )
        conn.commit()


# --------------------------------------------------------------- orders ----
async def create_order(
    telegram_id: int,
    product_id: int,
    product_name: str,
    quantity: int,
    unit_price_usd: float,
    idempotency_key: str,
) -> int:
    async with _lock:
        conn = _require_conn()
        now = time.time()
        cur = conn.execute(
            """INSERT INTO orders
               (telegram_id, product_id, product_name, quantity, unit_price_usd,
                total_price_usd, status, idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)""",
            (
                telegram_id,
                product_id,
                product_name,
                quantity,
                unit_price_usd,
                unit_price_usd * quantity,
                idempotency_key,
                now,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid


async def update_order_result(
    order_id: int,
    status: str,
    irmarket_order_id: Optional[int] = None,
    accounts_json: Optional[str] = None,
) -> None:
    async with _lock:
        conn = _require_conn()
        conn.execute(
            """UPDATE orders SET status = ?, irmarket_order_id = COALESCE(?, irmarket_order_id),
               accounts = COALESCE(?, accounts), updated_at = ? WHERE id = ?""",
            (status, irmarket_order_id, accounts_json, time.time(), order_id),
        )
        conn.commit()


async def update_order_status_by_irmarket_id(
    irmarket_order_id: int, status: str, accounts_json: Optional[str] = None
) -> Optional[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        conn.execute(
            """UPDATE orders SET status = ?, accounts = COALESCE(?, accounts), updated_at = ?
               WHERE irmarket_order_id = ?""",
            (status, accounts_json, time.time(), irmarket_order_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM orders WHERE irmarket_order_id = ?", (irmarket_order_id,)
        ).fetchone()


async def get_order(order_id: int) -> Optional[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


async def get_user_orders(telegram_id: int, limit: int = 10) -> list[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        return conn.execute(
            "SELECT * FROM orders WHERE telegram_id = ? ORDER BY created_at DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


async def recent_orders(limit: int = 20) -> list[sqlite3.Row]:
    async with _lock:
        conn = _require_conn()
        return conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


async def total_sales_usd() -> float:
    async with _lock:
        conn = _require_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(total_price_usd), 0) s FROM orders WHERE status = 'delivered'"
        ).fetchone()
        return row["s"]


# -------------------------------------------------------------- settings ----
async def set_setting(key: str, value: str) -> None:
    async with _lock:
        conn = _require_conn()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


async def get_setting(key: str) -> Optional[str]:
    async with _lock:
        conn = _require_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
