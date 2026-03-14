from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .db import create_connection, init_db, transaction


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class Ledger:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connection = create_connection(settings.db_path)
        init_db(self.connection)
        self.lock = threading.Lock()

    def initialize(self) -> None:
        self.import_legacy_csv_once()
        self.ensure_default_settings()
        self.ensure_auto_row()

    def ensure_default_settings(self) -> None:
        for key, value in {
            "walletAddress": self.settings.wallet_address,
            "defaultBudgetSol": self.settings.default_budget_sol,
            "budgetSolMax": self.settings.budget_sol_max,
            "defaultMode": self.settings.default_mode,
            "riskMode": "normal",
            "rankingType": self.settings.default_ranking_type,
            "minLiquidityUsd": self.settings.min_liquidity_usd,
            "stopLossPct": self.settings.stop_loss_pct,
            "takeProfitCostBasisPct": self.settings.take_profit_cost_basis_pct,
            "takeProfitHalfPct": self.settings.take_profit_half_pct,
            "moonbagTriggerPct": self.settings.moonbag_trigger_pct,
            "moonbagFraction": self.settings.moonbag_fraction,
            "maxHoldHours": self.settings.max_hold_hours,
            "timeExitMaxGainPct": self.settings.time_exit_max_gain_pct,
            "discoverInterval": self.settings.discover_interval,
            "orderPollInterval": self.settings.order_poll_interval,
            "orderPollMax": self.settings.order_poll_max,
            "autoDailyLossLimitSol": self.settings.auto_daily_loss_limit_sol,
            "autoMaxConsecutiveLosses": self.settings.auto_max_consecutive_losses,
            "reserveSolBalance": self.settings.reserve_sol_balance,
        }.items():
            if value in (None, ""):
                continue
            if self.get_setting(key) is None:
                self.set_setting(key, value)

    def ensure_auto_row(self) -> None:
        row = self.connection.execute("SELECT id FROM auto_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return
        self.connection.execute(
            """
            INSERT INTO auto_runs (
                running, mode, ranking_type, budget_sol, risk_mode, paused_reason, last_action,
                last_order_id, last_tx_id, started_at, stopped_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (0, "auto_live", "combined", self.settings.default_budget_sol, "normal", "", "", "", "", "", "", now_iso()),
        )
        self.connection.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if not row else str(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value), now_iso()),
        )
        self.connection.commit()

    def get_settings_payload(self) -> dict[str, Any]:
        payload = self.settings.as_public_dict()
        float_keys = {
            "defaultBudgetSol",
            "budgetSolMax",
            "minLiquidityUsd",
            "stopLossPct",
            "takeProfitCostBasisPct",
            "takeProfitHalfPct",
            "moonbagTriggerPct",
            "moonbagFraction",
            "maxHoldHours",
            "timeExitMaxGainPct",
            "autoDailyLossLimitSol",
            "reserveSolBalance",
        }
        int_keys = {"discoverInterval", "orderPollInterval", "orderPollMax", "autoMaxConsecutiveLosses"}
        for key in (
            "walletAddress",
            "defaultBudgetSol",
            "budgetSolMax",
            "defaultMode",
            "riskMode",
            "rankingType",
            "minLiquidityUsd",
            "stopLossPct",
            "takeProfitCostBasisPct",
            "takeProfitHalfPct",
            "moonbagTriggerPct",
            "moonbagFraction",
            "maxHoldHours",
            "timeExitMaxGainPct",
            "discoverInterval",
            "orderPollInterval",
            "orderPollMax",
            "autoDailyLossLimitSol",
            "autoMaxConsecutiveLosses",
            "reserveSolBalance",
        ):
            value = self.get_setting(key)
            if value is None:
                continue
            if key in float_keys:
                payload[key] = as_float(value)
            elif key in int_keys:
                payload[key] = int(as_float(value))
            else:
                payload[key] = value
        return payload

    def import_legacy_csv_once(self) -> None:
        if self.connection.execute("SELECT COUNT(*) AS count FROM trades").fetchone()["count"]:
            return
        legacy_root = self.settings.legacy_root
        if not legacy_root.exists():
            return
        trades_path = legacy_root / "trades.csv"
        positions_path = legacy_root / "positions.csv"
        pnl_path = legacy_root / "pnl.csv"
        if trades_path.exists():
            with trades_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    self.connection.execute(
                        """
                        INSERT INTO trades (
                            client_trade_id, order_id, side, token_symbol, token_contract,
                            amount_in_sol, amount_out, mode, status, tx_id, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row.get("order_id") or f"legacy-{row.get('timestamp', now_iso())}",
                            row.get("order_id", ""),
                            row.get("side", "buy"),
                            row.get("token_symbol", ""),
                            row.get("token_contract", ""),
                            as_float(row.get("amount_in_sol")),
                            as_float(row.get("quoted_amount_out")),
                            "legacy_import",
                            row.get("status", ""),
                            row.get("tx_id", ""),
                            row.get("note", ""),
                            row.get("timestamp", now_iso()),
                        ),
                    )
        if positions_path.exists():
            with positions_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    self.connection.execute(
                        """
                        INSERT INTO position_records (
                            token_contract, token_symbol, entry_price_sol, current_price_sol,
                            amount, market_value_sol, cost_basis_sol, realized_pnl_sol, peak_price_sol,
                            take_profit_stage, mode, opened_at, closed_at, status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row.get("token_contract", ""),
                            row.get("token_symbol", ""),
                            as_float(row.get("entry_price_sol")),
                            as_float(row.get("current_price_sol")),
                            as_float(row.get("amount")),
                            as_float(row.get("market_value_sol")),
                            as_float(row.get("cost_basis_sol")),
                            as_float(row.get("realized_pnl_sol")),
                            as_float(row.get("peak_price_sol")),
                            row.get("take_profit_stage") or "entry",
                            "legacy_import",
                            row.get("opened_at") or row.get("timestamp") or now_iso(),
                            row.get("closed_at") or None,
                            row.get("status") or "open",
                            row.get("timestamp") or now_iso(),
                        ),
                    )
        if pnl_path.exists():
            with pnl_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    self.connection.execute(
                        """
                        INSERT INTO pnl_snapshots (mode, realized, unrealized, total, open_positions, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "legacy_import",
                            as_float(row.get("realized_pnl_sol")),
                            as_float(row.get("unrealized_pnl_sol")),
                            as_float(row.get("total_pnl_sol")),
                            int(as_float(row.get("open_positions"))),
                            row.get("timestamp") or now_iso(),
                        ),
                    )
        self.connection.commit()

    def record_api_event(
        self,
        *,
        endpoint: str,
        http_status: int | None,
        error_type: str,
        retry_count: int,
        breaker_state: str,
        detail: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO api_events (endpoint, http_status, error_type, retry_count, breaker_state, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (endpoint, http_status, error_type, retry_count, breaker_state, detail[:500], now_iso()),
        )
        self.connection.commit()

    def record_risk_event(self, event_type: str, detail: str = "") -> None:
        self.connection.execute(
            "INSERT INTO risk_events (event_type, detail, created_at) VALUES (?, ?, ?)",
            (event_type, detail[:500], now_iso()),
        )
        self.connection.commit()

    def create_order(self, payload: dict[str, Any]) -> None:
        with self.lock, transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO orders (
                    order_id, client_trade_id, position_id, side, token_symbol, token_contract, mode, status,
                    market, tx_id, amount_in_sol, amount_out, error_reason, raw_order_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("order_id"),
                    payload["client_trade_id"],
                    payload.get("position_id"),
                    payload["side"],
                    payload["token_symbol"],
                    payload["token_contract"],
                    payload["mode"],
                    payload["status"],
                    payload.get("market"),
                    payload.get("tx_id"),
                    payload.get("amount_in_sol", 0.0),
                    payload.get("amount_out", 0.0),
                    payload.get("error_reason", ""),
                    json.dumps(payload.get("raw_order_json", {}), ensure_ascii=True),
                    payload.get("created_at", now_iso()),
                    payload.get("updated_at", now_iso()),
                ),
            )

    def update_order(self, client_trade_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [now_iso(), client_trade_id]
        self.connection.execute(
            f"UPDATE orders SET {assignments}, updated_at = ? WHERE client_trade_id = ?",
            values,
        )
        self.connection.commit()

    def get_order(self, order_id: str | None = None, client_trade_id: str | None = None) -> dict[str, Any] | None:
        if order_id:
            return self.connection.execute("SELECT * FROM orders WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
        if client_trade_id:
            return self.connection.execute("SELECT * FROM orders WHERE client_trade_id = ?", (client_trade_id,)).fetchone()
        return None

    def get_active_order(self) -> dict[str, Any] | None:
        return self.connection.execute(
            "SELECT * FROM orders WHERE status IN ('prepared', 'submitted', 'polling') ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def add_trade(self, payload: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO trades (
                client_trade_id, order_id, position_id, side, token_symbol, token_contract, amount_in_sol,
                amount_out, mode, status, tx_id, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["client_trade_id"],
                payload.get("order_id", ""),
                payload.get("position_id"),
                payload["side"],
                payload["token_symbol"],
                payload["token_contract"],
                payload.get("amount_in_sol", 0.0),
                payload.get("amount_out", 0.0),
                payload["mode"],
                payload["status"],
                payload.get("tx_id", ""),
                payload.get("note", ""),
                payload.get("created_at", now_iso()),
            ),
        )
        self.connection.commit()

    def has_trade(self, client_trade_id: str) -> bool:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM trades WHERE client_trade_id = ?",
            (client_trade_id,),
        ).fetchone()
        return bool(row and int(row["count"]) > 0)

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(
            self.connection.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        )

    def upsert_position(self, payload: dict[str, Any]) -> None:
        position_id = payload.get("id") or payload.get("position_id")
        values = (
            payload["token_contract"],
            payload["token_symbol"],
            payload.get("entry_price_sol", 0.0),
            payload.get("current_price_sol", 0.0),
            payload.get("amount", 0.0),
            payload.get("market_value_sol", 0.0),
            payload.get("cost_basis_sol", 0.0),
            payload.get("realized_pnl_sol", 0.0),
            payload.get("peak_price_sol", 0.0),
            payload.get("take_profit_stage", "entry"),
            payload.get("mode", "paper"),
            payload.get("opened_at", now_iso()),
            payload.get("closed_at"),
            payload.get("status", "open"),
            payload.get("updated_at", now_iso()),
        )
        if position_id:
            self.connection.execute(
                """
                UPDATE position_records
                SET token_contract = ?, token_symbol = ?, entry_price_sol = ?, current_price_sol = ?,
                    amount = ?, market_value_sol = ?, cost_basis_sol = ?, realized_pnl_sol = ?,
                    peak_price_sol = ?, take_profit_stage = ?, mode = ?, opened_at = ?, closed_at = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                values + (position_id,),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO position_records (
                    token_contract, token_symbol, entry_price_sol, current_price_sol, amount,
                    market_value_sol, cost_basis_sol, realized_pnl_sol, peak_price_sol,
                    take_profit_stage, mode, opened_at, closed_at, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        self.connection.commit()

    def list_positions(self) -> list[dict[str, Any]]:
        return list(self.connection.execute("SELECT * FROM position_records ORDER BY updated_at DESC, id DESC").fetchall())

    def open_positions(self) -> list[dict[str, Any]]:
        return list(
            self.connection.execute(
                "SELECT * FROM position_records WHERE status = 'open' ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        )

    def get_position(self, position_id: int) -> dict[str, Any] | None:
        return self.connection.execute("SELECT * FROM position_records WHERE id = ?", (position_id,)).fetchone()

    def latest_open_position(self, token_contract: str | None = None) -> dict[str, Any] | None:
        if token_contract:
            return self.connection.execute(
                """
                SELECT * FROM position_records
                WHERE token_contract = ? AND status = 'open'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (token_contract,),
            ).fetchone()
        return self.connection.execute(
            "SELECT * FROM position_records WHERE status = 'open' ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()

    def latest_position(self, token_contract: str | None = None) -> dict[str, Any] | None:
        if token_contract:
            return self.connection.execute(
                "SELECT * FROM position_records WHERE token_contract = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (token_contract,),
            ).fetchone()
        return self.connection.execute("SELECT * FROM position_records ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()

    def add_pnl_snapshot(
        self,
        mode: str,
        realized: float,
        unrealized: float,
        open_positions: int,
        *,
        token_symbol: str = "",
        token_contract: str = "",
    ) -> None:
        total = realized + unrealized
        self.connection.execute(
            """
            INSERT INTO pnl_snapshots (mode, token_symbol, token_contract, realized, unrealized, total, open_positions, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mode, token_symbol, token_contract, realized, unrealized, total, open_positions, now_iso()),
        )
        self.connection.commit()

    def list_pnl(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(self.connection.execute("SELECT * FROM pnl_snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall())

    def pnl_by_token(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                token_symbol,
                token_contract,
                mode,
                MAX(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS has_open,
                SUM(CASE WHEN status = 'open' THEN amount ELSE 0 END) AS amount,
                SUM(CASE WHEN status = 'open' THEN cost_basis_sol ELSE 0 END) AS cost_basis_sol,
                SUM(CASE WHEN status = 'open' THEN market_value_sol ELSE 0 END) AS market_value_sol,
                SUM(realized_pnl_sol) AS realized_pnl_sol,
                MAX(updated_at) AS updated_at,
                MAX(closed_at) AS closed_at
            FROM position_records
            GROUP BY token_symbol, token_contract, mode
            ORDER BY MAX(updated_at) DESC
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            cost_basis = as_float(row["cost_basis_sol"])
            market_value = as_float(row["market_value_sol"])
            realized = as_float(row["realized_pnl_sol"])
            status = "open" if int(row.get("has_open") or 0) else "closed"
            unrealized = 0.0 if status == "closed" else market_value - cost_basis
            results.append(
                {
                    "token_symbol": row["token_symbol"],
                    "token_contract": row["token_contract"],
                    "mode": row["mode"],
                    "status": status,
                    "amount": as_float(row["amount"]),
                    "cost_basis_sol": cost_basis,
                    "market_value_sol": market_value,
                    "realized": realized,
                    "unrealized": unrealized,
                    "total": realized + unrealized,
                    "updated_at": row["updated_at"],
                    "closed_at": row["closed_at"],
                }
            )
        return results

    def pnl_overview(self) -> dict[str, Any]:
        trade_counts = self.connection.execute(
            """
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) AS buy_trades,
                SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sell_trades
            FROM trades
            """
        ).fetchone() or {}
        position_counts = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_positions,
                SUM(CASE WHEN status = 'closed' AND realized_pnl_sol > 0 THEN 1 ELSE 0 END) AS profitable_positions,
                SUM(CASE WHEN status = 'closed' AND realized_pnl_sol < 0 THEN 1 ELSE 0 END) AS losing_positions
            FROM position_records
            """
        ).fetchone() or {}
        pnl_totals = self.connection.execute(
            """
            SELECT
                SUM(realized_pnl_sol) AS realized_total,
                SUM(CASE WHEN status = 'open' THEN market_value_sol - cost_basis_sol ELSE 0 END) AS unrealized_total
            FROM position_records
            """
        ).fetchone() or {}
        realized_total = as_float(pnl_totals.get("realized_total"))
        unrealized_total = as_float(pnl_totals.get("unrealized_total"))
        return {
            "totalTrades": int(trade_counts.get("total_trades") or 0),
            "buyTrades": int(trade_counts.get("buy_trades") or 0),
            "sellTrades": int(trade_counts.get("sell_trades") or 0),
            "openPositions": int(position_counts.get("open_positions") or 0),
            "closedPositions": int(position_counts.get("closed_positions") or 0),
            "profitablePositions": int(position_counts.get("profitable_positions") or 0),
            "losingPositions": int(position_counts.get("losing_positions") or 0),
            "realizedPnlSol": realized_total,
            "unrealizedPnlSol": unrealized_total,
            "totalPnlSol": realized_total + unrealized_total,
        }

    def latest_pnl(self) -> dict[str, Any] | None:
        return self.connection.execute("SELECT * FROM pnl_snapshots ORDER BY id DESC LIMIT 1").fetchone()

    def summarize(self) -> dict[str, Any]:
        latest_trade = self.connection.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        latest_pnl = self.latest_pnl()
        auto_status = self.get_auto_status()
        return {
            "latestTrade": latest_trade or {},
            "latestPnl": latest_pnl or {"realized": 0.0, "unrealized": 0.0, "total": 0.0, "open_positions": 0},
            "positions": self.open_positions(),
            "autoStatus": auto_status,
            "settings": self.get_settings_payload(),
        }

    def start_auto_run(self, *, ranking_type: str, budget_sol: float, risk_mode: str) -> None:
        self.connection.execute(
            """
            UPDATE auto_runs
            SET running = 1, mode = 'auto_live', ranking_type = ?, budget_sol = ?, risk_mode = ?,
                paused_reason = '', last_action = 'started', started_at = ?, stopped_at = '', updated_at = ?
            WHERE id = (SELECT id FROM auto_runs ORDER BY id DESC LIMIT 1)
            """,
            (ranking_type, budget_sol, risk_mode, now_iso(), now_iso()),
        )
        self.connection.commit()

    def stop_auto_run(self, paused_reason: str = "manual_stop") -> None:
        self.connection.execute(
            """
            UPDATE auto_runs
            SET running = 0, paused_reason = ?, stopped_at = ?, updated_at = ?
            WHERE id = (SELECT id FROM auto_runs ORDER BY id DESC LIMIT 1)
            """,
            (paused_reason, now_iso(), now_iso()),
        )
        self.connection.commit()

    def update_auto_run(self, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [now_iso()]
        self.connection.execute(
            f"UPDATE auto_runs SET {assignments}, updated_at = ? WHERE id = (SELECT id FROM auto_runs ORDER BY id DESC LIMIT 1)",
            values,
        )
        self.connection.commit()

    def get_auto_status(self) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM auto_runs ORDER BY id DESC LIMIT 1").fetchone() or {}
        latest_pnl = self.latest_pnl() or {}
        consecutive_losses = self.count_recent_losses()
        return {
            "enabled": bool(self.settings.private_key_sol),
            "running": bool(row.get("running", 0)),
            "pausedReason": row.get("paused_reason", ""),
            "currentMode": row.get("mode", "auto_live"),
            "rankingType": row.get("ranking_type", "combined"),
            "budgetSol": row.get("budget_sol", self.settings.default_budget_sol),
            "riskMode": row.get("risk_mode", "normal"),
            "lastAction": row.get("last_action", ""),
            "lastOrderId": row.get("last_order_id", ""),
            "lastTxId": row.get("last_tx_id", ""),
            "realizedPnlSol": latest_pnl.get("realized", 0.0),
            "unrealizedPnlSol": latest_pnl.get("unrealized", 0.0),
            "todayPnlSol": self.today_pnl(),
            "consecutiveLosses": consecutive_losses,
            "openPositions": len(self.open_positions()),
        }

    def clear_paper_data(self) -> dict[str, int]:
        with self.lock, transaction(self.connection):
            order_count = self.connection.execute("SELECT COUNT(*) AS count FROM orders WHERE mode = 'paper'").fetchone()["count"]
            trade_count = self.connection.execute("SELECT COUNT(*) AS count FROM trades WHERE mode = 'paper'").fetchone()["count"]
            position_count = self.connection.execute(
                "SELECT COUNT(*) AS count FROM position_records WHERE mode = 'paper'"
            ).fetchone()["count"]
            pnl_count = self.connection.execute("SELECT COUNT(*) AS count FROM pnl_snapshots WHERE mode = 'paper'").fetchone()["count"]
            self.connection.execute("DELETE FROM orders WHERE mode = 'paper'")
            self.connection.execute("DELETE FROM trades WHERE mode = 'paper'")
            self.connection.execute("DELETE FROM position_records WHERE mode = 'paper'")
            self.connection.execute("DELETE FROM pnl_snapshots WHERE mode = 'paper'")
        return {
            "orders": int(order_count),
            "trades": int(trade_count),
            "positions": int(position_count),
            "pnlSnapshots": int(pnl_count),
        }

    def clear_all_history_data(self) -> dict[str, int]:
        with self.lock, transaction(self.connection):
            counts = {
                "orders": int(self.connection.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]),
                "trades": int(self.connection.execute("SELECT COUNT(*) AS count FROM trades").fetchone()["count"]),
                "positions": int(self.connection.execute("SELECT COUNT(*) AS count FROM position_records").fetchone()["count"]),
                "pnlSnapshots": int(self.connection.execute("SELECT COUNT(*) AS count FROM pnl_snapshots").fetchone()["count"]),
                "riskEvents": int(self.connection.execute("SELECT COUNT(*) AS count FROM risk_events").fetchone()["count"]),
                "apiEvents": int(self.connection.execute("SELECT COUNT(*) AS count FROM api_events").fetchone()["count"]),
            }
            self.connection.execute("DELETE FROM orders")
            self.connection.execute("DELETE FROM trades")
            self.connection.execute("DELETE FROM position_records")
            self.connection.execute("DELETE FROM pnl_snapshots")
            self.connection.execute("DELETE FROM risk_events")
            self.connection.execute("DELETE FROM api_events")
            self.connection.execute(
                """
                UPDATE auto_runs
                SET running = 0, paused_reason = 'manual_stop', last_action = '', last_order_id = '',
                    last_tx_id = '', started_at = '', stopped_at = ?, updated_at = ?
                WHERE id = (SELECT id FROM auto_runs ORDER BY id DESC LIMIT 1)
                """,
                (now_iso(), now_iso()),
            )
        return counts

    def count_recent_losses(self, limit: int = 3) -> int:
        rows = self.connection.execute(
            "SELECT total FROM pnl_snapshots WHERE mode = 'auto_live' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        count = 0
        for row in rows:
            if as_float(row["total"]) < 0:
                count += 1
            else:
                break
        return count

    def today_pnl(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.connection.execute(
            "SELECT realized FROM pnl_snapshots WHERE created_at LIKE ? AND mode IN ('auto_live', 'semi_auto_live') ORDER BY id DESC LIMIT 1",
            (f"{today}%",),
        ).fetchall()
        return as_float(rows[0]["realized"]) if rows else 0.0
