"""
SQLite database layer for Aaminata Order Tracker.
"""

import sqlite3
import os
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "aaminata.db")


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS customers (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT    NOT NULL,
                    phone      TEXT,
                    address    TEXT,
                    notes      TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    items       TEXT    NOT NULL,
                    total       REAL    NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'Pending',
                    notes       TEXT,
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
                CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
                CREATE INDEX IF NOT EXISTS idx_orders_date     ON orders(created_at);
            """)

    # ── Customers ──────────────────────────────────────────────────────────────

    def add_customer(self, name: str, phone: Optional[str] = None,
                     address: Optional[str] = None, notes: Optional[str] = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO customers (name, phone, address, notes) VALUES (?,?,?,?)",
                (name, phone, address, notes),
            )
            return cur.lastrowid

    def get_customer_by_id(self, cid: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
            return dict(row) if row else None

    def get_all_customers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def search_customers(self, query: str) -> list[dict]:
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? ORDER BY name",
                (pattern, pattern),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Orders ─────────────────────────────────────────────────────────────────

    def add_order(self, customer_id: int, items: str, total: float,
                  notes: Optional[str] = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO orders (customer_id, items, total, notes) VALUES (?,?,?,?)",
                (customer_id, items, total, notes),
            )
            return cur.lastrowid

    def get_order_by_id(self, oid: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone
                FROM orders o JOIN customers c ON o.customer_id = c.id
                WHERE o.id = ?
            """, (oid,)).fetchone()
            return dict(row) if row else None

    def get_all_orders(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone
                FROM orders o JOIN customers c ON o.customer_id = c.id
                ORDER BY o.id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_todays_orders(self) -> list[dict]:
        """Orders created today (local date)."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone
                FROM orders o JOIN customers c ON o.customer_id = c.id
                WHERE date(o.created_at) = date('now','localtime')
                ORDER BY o.id DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_orders_by_customer(self, customer_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC",
                (customer_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_order_status(self, oid: int, status: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE orders SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                (status, oid),
            )
