from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _dict_factory(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> dict[str, object]:
    return {column[0]: row[idx] for idx, column in enumerate(cursor.description)}


def create_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = _dict_factory
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    return connection


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        client_trade_id TEXT NOT NULL UNIQUE,
        position_id INTEGER,
        side TEXT NOT NULL,
        token_symbol TEXT NOT NULL,
        token_contract TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        market TEXT,
        tx_id TEXT,
        amount_in_sol REAL NOT NULL DEFAULT 0,
        amount_out REAL NOT NULL DEFAULT 0,
        error_reason TEXT,
        raw_order_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_trade_id TEXT NOT NULL,
        order_id TEXT,
        position_id INTEGER,
        side TEXT NOT NULL,
        token_symbol TEXT NOT NULL,
        token_contract TEXT NOT NULL,
        amount_in_sol REAL NOT NULL DEFAULT 0,
        amount_out REAL NOT NULL DEFAULT 0,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        tx_id TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        token_contract TEXT PRIMARY KEY,
        token_symbol TEXT NOT NULL,
        entry_price_sol REAL NOT NULL DEFAULT 0,
        current_price_sol REAL NOT NULL DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        market_value_sol REAL NOT NULL DEFAULT 0,
        cost_basis_sol REAL NOT NULL DEFAULT 0,
        realized_pnl_sol REAL NOT NULL DEFAULT 0,
        peak_price_sol REAL NOT NULL DEFAULT 0,
        take_profit_stage TEXT NOT NULL DEFAULT 'entry',
        mode TEXT NOT NULL DEFAULT 'paper',
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_contract TEXT NOT NULL,
        token_symbol TEXT NOT NULL,
        entry_price_sol REAL NOT NULL DEFAULT 0,
        current_price_sol REAL NOT NULL DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        market_value_sol REAL NOT NULL DEFAULT 0,
        cost_basis_sol REAL NOT NULL DEFAULT 0,
        realized_pnl_sol REAL NOT NULL DEFAULT 0,
        peak_price_sol REAL NOT NULL DEFAULT 0,
        take_profit_stage TEXT NOT NULL DEFAULT 'entry',
        mode TEXT NOT NULL DEFAULT 'paper',
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pnl_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        token_symbol TEXT NOT NULL DEFAULT '',
        token_contract TEXT NOT NULL DEFAULT '',
        realized REAL NOT NULL DEFAULT 0,
        unrealized REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL DEFAULT 0,
        open_positions INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auto_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        running INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL,
        ranking_type TEXT NOT NULL DEFAULT 'combined',
        budget_sol REAL NOT NULL DEFAULT 0,
        risk_mode TEXT NOT NULL DEFAULT 'normal',
        paused_reason TEXT,
        last_action TEXT NOT NULL DEFAULT '',
        last_order_id TEXT NOT NULL DEFAULT '',
        last_tx_id TEXT NOT NULL DEFAULT '',
        started_at TEXT,
        stopped_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        http_status INTEGER,
        error_type TEXT NOT NULL DEFAULT '',
        retry_count INTEGER NOT NULL DEFAULT 0,
        breaker_state TEXT NOT NULL DEFAULT 'closed',
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_single_active
    ON orders(status)
    WHERE status IN ('prepared', 'submitted', 'polling')
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_position_records_contract_status
    ON position_records(token_contract, status, updated_at)
    """,
]


def init_db(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        connection.execute(statement)
    pnl_columns = {row["name"] for row in connection.execute("PRAGMA table_info(pnl_snapshots)").fetchall()}
    if "token_symbol" not in pnl_columns:
        connection.execute("ALTER TABLE pnl_snapshots ADD COLUMN token_symbol TEXT NOT NULL DEFAULT ''")
    if "token_contract" not in pnl_columns:
        connection.execute("ALTER TABLE pnl_snapshots ADD COLUMN token_contract TEXT NOT NULL DEFAULT ''")
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(auto_runs)").fetchall()}
    if "ranking_type" not in columns:
        connection.execute("ALTER TABLE auto_runs ADD COLUMN ranking_type TEXT NOT NULL DEFAULT 'combined'")
    order_columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)").fetchall()}
    if "position_id" not in order_columns:
        connection.execute("ALTER TABLE orders ADD COLUMN position_id INTEGER")
    trade_columns = {row["name"] for row in connection.execute("PRAGMA table_info(trades)").fetchall()}
    if "position_id" not in trade_columns:
        connection.execute("ALTER TABLE trades ADD COLUMN position_id INTEGER")
    records_count = connection.execute("SELECT COUNT(*) AS count FROM position_records").fetchone()["count"]
    legacy_positions_exist = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not records_count and legacy_positions_exist:
        connection.execute(
            """
            INSERT INTO position_records (
                token_contract, token_symbol, entry_price_sol, current_price_sol, amount,
                market_value_sol, cost_basis_sol, realized_pnl_sol, peak_price_sol,
                take_profit_stage, mode, opened_at, closed_at, status, updated_at
            )
            SELECT
                token_contract, token_symbol, entry_price_sol, current_price_sol, amount,
                market_value_sol, cost_basis_sol, realized_pnl_sol, peak_price_sol,
                take_profit_stage, mode, opened_at, closed_at, status, updated_at
            FROM positions
            """
        )
    connection.commit()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
