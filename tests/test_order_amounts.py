from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.api_routes import (
    _open_position_amount,
    _refresh_or_clear_active_order,
    _require_order_data,
    _resolve_buy_amount,
    _resume_active_live_order,
)
from backend.config import Settings
from backend.ledger import Ledger, now_iso
from backend.models import OrderExecuteRequest
from backend.runner import Runner
from backend.runner import ServiceError
from backend.strategy import StrategyDefaults, StrategyEngine


class DummyClient:
    pass


class DummyStrategy(StrategyEngine):
    def __init__(self) -> None:
        super().__init__(
            Runner(DummyClient()),  # type: ignore[arg-type]
            StrategyDefaults(
                min_liquidity_usd=1.0,
                stop_loss_pct=0.1,
                take_profit_cost_basis_pct=0.2,
                take_profit_half_pct=0.5,
                moonbag_trigger_pct=1.0,
                moonbag_fraction=0.1,
                max_hold_hours=1.0,
                time_exit_max_gain_pct=0.1,
            ),
        )

    def current_price_in_sol(self, token_contract: str, token_fallback_price_usd: float = 0.0) -> tuple[float, float]:
        return 0.5, 100.0


class OrderAmountTests(unittest.TestCase):
    def test_require_order_data_rejects_empty_data(self) -> None:
        with self.assertRaisesRegex(Exception, "empty order data"):
            _require_order_data({"status": 0, "data": None}, "order_create")

    def test_resolve_buy_amount_falls_back_to_spot_price(self) -> None:
        strategy = DummyStrategy()
        amount = _resolve_buy_amount(
            quoted_amount=0.0,
            spend_amount=1.0,
            token_contract="token",
            strategy=strategy,
            current_price_sol=0.5,
        )
        self.assertEqual(amount, 2.0)

    def test_open_position_amount_requires_positive_open_position(self) -> None:
        with self.assertRaisesRegex(Exception, "zero"):
            _open_position_amount({"status": "open", "amount": 0.0})

    def test_resume_prepared_auto_sell_order_reuses_existing_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            settings.private_key_sol = "secret"
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.upsert_position(
                {
                    "token_contract": "contract-1",
                    "token_symbol": "TOK",
                    "entry_price_sol": 0.01,
                    "current_price_sol": 0.02,
                    "amount": 5.0,
                    "market_value_sol": 0.1,
                    "cost_basis_sol": 0.05,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": 0.02,
                    "take_profit_stage": "entry",
                    "mode": "auto_live",
                    "opened_at": now_iso(),
                    "status": "open",
                    "updated_at": now_iso(),
                }
            )
            ledger.create_order(
                {
                    "order_id": "order-1",
                    "client_trade_id": "auto-test-1",
                    "side": "sell",
                    "token_symbol": "TOK",
                    "token_contract": "contract-1",
                    "mode": "auto_live",
                    "status": "prepared",
                    "market": "mock-market",
                    "amount_in_sol": 0.0,
                    "amount_out": 0.1,
                    "raw_order_json": {"data": {"orderId": "order-1"}},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
            payload = OrderExecuteRequest(
                side="sell",
                tokenContract="contract-1",
                tokenAmount=5.0,
                mode="auto_live",
                walletAddress="wallet",
            )
            runner = Mock()
            runner.sign_order.return_value = ["signed"]
            runner.order_submit.return_value = {"status": 0, "data": {"ok": True}}
            runner.order_status.return_value = {"data": {"status": "success", "receiveAmount": 0.11}, "txs": [{"txId": "tx-1"}]}
            strategy = DummyStrategy()

            result = _resume_active_live_order(
                active_order=ledger.get_active_order(),
                payload=payload,
                ledger=ledger,
                runner=runner,
                settings=settings,
                strategy=strategy,
                allow_submit=True,
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["resumed"])
            runner.sign_order.assert_called_once()
            runner.order_submit.assert_called_once_with("order-1", ["signed"])
            position = ledger.latest_position("contract-1")
            self.assertEqual(position["status"], "closed")

    def test_refresh_or_clear_active_order_allows_new_trade_when_remote_order_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.create_order(
                {
                    "order_id": "order-missing",
                    "client_trade_id": "auto-test-missing",
                    "side": "sell",
                    "token_symbol": "TOK",
                    "token_contract": "contract-1",
                    "mode": "auto_live",
                    "status": "submitted",
                    "market": "mock-market",
                    "amount_in_sol": 0.0,
                    "amount_out": 0.1,
                    "raw_order_json": {"data": {"orderId": "order-missing"}},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
            runner = Mock()
            runner.order_status.side_effect = ServiceError("HTTP 404: order not found")
            strategy = DummyStrategy()

            blocking = _refresh_or_clear_active_order(
                active_order=ledger.get_active_order(),
                ledger=ledger,
                runner=runner,
                strategy=strategy,
                settings=settings,
            )

            self.assertIsNone(blocking)
            refreshed = ledger.get_order(client_trade_id="auto-test-missing")
            self.assertEqual(refreshed["status"], "failed")
            self.assertEqual(refreshed["error_reason"], "remote_order_not_found")


if __name__ == "__main__":
    unittest.main()
