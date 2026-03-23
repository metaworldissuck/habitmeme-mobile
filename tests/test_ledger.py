from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.config import Settings
from backend.ledger import Ledger


class LedgerTests(unittest.TestCase):
    def test_settings_payload_includes_position_sizing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            settings.position_sizing_mode = "equal_remaining"
            ledger = Ledger(settings)
            ledger.initialize()
            payload = ledger.get_settings_payload()
            self.assertEqual(payload["positionSizingMode"], "equal_remaining")

    def test_same_token_positions_are_preserved_as_history_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            first_opened_at = "2026-03-13T00:00:00+00:00"
            second_opened_at = "2026-03-14T00:00:00+00:00"
            ledger.upsert_position(
                {
                    "token_contract": "same-contract",
                    "token_symbol": "SAME",
                    "entry_price_sol": 0.001,
                    "current_price_sol": 0.0012,
                    "amount": 100.0,
                    "market_value_sol": 0.12,
                    "cost_basis_sol": 0.1,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": 0.0012,
                    "take_profit_stage": "entry",
                    "mode": "paper",
                    "opened_at": first_opened_at,
                    "status": "open",
                    "updated_at": first_opened_at,
                }
            )
            ledger.upsert_position(
                {
                    "token_contract": "same-contract",
                    "token_symbol": "SAME",
                    "entry_price_sol": 0.002,
                    "current_price_sol": 0.0025,
                    "amount": 50.0,
                    "market_value_sol": 0.125,
                    "cost_basis_sol": 0.1,
                    "realized_pnl_sol": 0.01,
                    "peak_price_sol": 0.0025,
                    "take_profit_stage": "entry",
                    "mode": "paper",
                    "opened_at": second_opened_at,
                    "status": "open",
                    "updated_at": second_opened_at,
                }
            )
            rows = ledger.list_positions()
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["id"], rows[1]["id"])
            self.assertEqual(len(ledger.open_positions()), 2)
            aggregated = ledger.pnl_by_token()
            self.assertEqual(len(aggregated), 1)
            self.assertAlmostEqual(aggregated[0]["amount"], 150.0, places=9)
            self.assertAlmostEqual(aggregated[0]["cost_basis_sol"], 0.2, places=9)
            self.assertAlmostEqual(aggregated[0]["realized"], 0.01, places=9)

    def test_ledger_summary_and_auto_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.02, risk_mode="normal")
            ledger.create_order(
                {
                    "order_id": "paper-order",
                    "client_trade_id": "paper-trade",
                    "side": "buy",
                    "token_symbol": "PAPER",
                    "token_contract": "paper-contract",
                    "mode": "paper",
                    "status": "success",
                }
            )
            ledger.add_trade(
                {
                    "client_trade_id": "paper-trade",
                    "order_id": "paper-order",
                    "side": "buy",
                    "token_symbol": "PAPER",
                    "token_contract": "paper-contract",
                    "amount_in_sol": 0.02,
                    "amount_out": 100.0,
                    "mode": "paper",
                    "status": "success",
                }
            )
            ledger.upsert_position(
                {
                    "token_contract": "paper-contract",
                    "token_symbol": "PAPER",
                    "entry_price_sol": 0.001,
                    "current_price_sol": 0.0012,
                    "amount": 100.0,
                    "market_value_sol": 0.12,
                    "cost_basis_sol": 0.1,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": 0.0012,
                    "take_profit_stage": "entry",
                    "mode": "paper",
                    "opened_at": "2026-03-13T00:00:00+00:00",
                    "status": "open",
                    "updated_at": "2026-03-13T00:00:00+00:00",
                }
            )
            ledger.upsert_position(
                {
                    "token_contract": "closed-contract",
                    "token_symbol": "CLOSED",
                    "entry_price_sol": 0.002,
                    "current_price_sol": 0.0,
                    "amount": 0.0,
                    "market_value_sol": 0.0,
                    "cost_basis_sol": 0.0,
                    "realized_pnl_sol": 0.03,
                    "peak_price_sol": 0.003,
                    "take_profit_stage": "half_taken",
                    "mode": "paper",
                    "opened_at": "2026-03-12T00:00:00+00:00",
                    "closed_at": "2026-03-12T12:00:00+00:00",
                    "status": "closed",
                    "updated_at": "2026-03-12T12:00:00+00:00",
                }
            )
            ledger.add_pnl_snapshot("auto_live", realized=0.01, unrealized=-0.001, open_positions=1, token_symbol="AUTO", token_contract="auto-contract")
            summary = ledger.summarize()
            self.assertEqual(summary["latestPnl"]["realized"], 0.01)
            self.assertEqual(summary["latestPnl"]["token_symbol"], "AUTO")
            self.assertTrue(summary["autoStatus"]["running"])
            self.assertEqual(summary["settings"]["defaultBudgetSol"], 0.02)
            self.assertEqual(summary["settings"]["budgetSolMax"], 0.1)
            self.assertEqual(summary["settings"]["minLiquidityUsd"], 60000.0)
            self.assertEqual(summary["settings"]["rankingType"], "combined")
            self.assertEqual(summary["autoStatus"]["rankingType"], "combined")
            pnl_overview = ledger.pnl_overview()
            self.assertEqual(pnl_overview["totalTrades"], 1)
            self.assertEqual(pnl_overview["buyTrades"], 1)
            self.assertEqual(pnl_overview["sellTrades"], 0)
            self.assertEqual(pnl_overview["openPositions"], 1)
            self.assertEqual(pnl_overview["closedPositions"], 1)
            self.assertEqual(pnl_overview["profitablePositions"], 1)
            self.assertEqual(pnl_overview["losingPositions"], 0)
            self.assertAlmostEqual(pnl_overview["grossBuySol"], 0.02, places=9)
            self.assertAlmostEqual(pnl_overview["realizedPnlSol"], 0.03, places=9)
            self.assertAlmostEqual(pnl_overview["unrealizedPnlSol"], 0.02, places=9)
            self.assertAlmostEqual(pnl_overview["totalPnlSol"], 0.05, places=9)
            self.assertAlmostEqual(pnl_overview["totalPnlPct"], 2.5, places=9)
            pnl_rows = ledger.pnl_by_token()
            self.assertEqual(len(pnl_rows), 2)
            self.assertEqual(pnl_rows[0]["token_symbol"], "PAPER")
            self.assertEqual(pnl_rows[0]["status"], "open")
            self.assertAlmostEqual(pnl_rows[0]["unrealized"], 0.02, places=9)

            cleared = ledger.clear_paper_data()
            self.assertEqual(cleared["orders"], 1)
            self.assertEqual(cleared["trades"], 1)
            self.assertEqual(cleared["positions"], 2)
            self.assertEqual(cleared["pnlSnapshots"], 0)
            self.assertFalse(any(position["mode"] == "paper" for position in ledger.list_positions()))

    def test_clear_all_history_keeps_settings_but_removes_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.set_setting("walletAddress", "wallet-1")
            ledger.create_order(
                {
                    "order_id": "order-1",
                    "client_trade_id": "trade-1",
                    "side": "buy",
                    "token_symbol": "TOK",
                    "token_contract": "contract-1",
                    "mode": "auto_live",
                    "status": "success",
                }
            )
            ledger.add_trade(
                {
                    "client_trade_id": "trade-1",
                    "order_id": "order-1",
                    "side": "buy",
                    "token_symbol": "TOK",
                    "token_contract": "contract-1",
                    "amount_in_sol": 0.02,
                    "amount_out": 100.0,
                    "mode": "auto_live",
                    "status": "success",
                }
            )
            ledger.upsert_position(
                {
                    "token_contract": "contract-1",
                    "token_symbol": "TOK",
                    "entry_price_sol": 0.001,
                    "current_price_sol": 0.0012,
                    "amount": 100.0,
                    "market_value_sol": 0.12,
                    "cost_basis_sol": 0.1,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": 0.0012,
                    "take_profit_stage": "entry",
                    "mode": "auto_live",
                    "opened_at": "2026-03-13T00:00:00+00:00",
                    "status": "open",
                    "updated_at": "2026-03-13T00:00:00+00:00",
                }
            )
            ledger.add_pnl_snapshot("auto_live", realized=0.01, unrealized=0.02, open_positions=1, token_symbol="TOK", token_contract="contract-1")
            ledger.record_risk_event("rate_limited", "test")
            ledger.record_api_event(endpoint="token-info", http_status=429, error_type="rate_limited", retry_count=1, breaker_state="closed")

            cleared = ledger.clear_all_history_data()

            self.assertEqual(cleared["orders"], 1)
            self.assertEqual(cleared["trades"], 1)
            self.assertEqual(cleared["positions"], 1)
            self.assertEqual(cleared["pnlSnapshots"], 1)
            self.assertEqual(cleared["riskEvents"], 1)
            self.assertEqual(cleared["apiEvents"], 1)
            self.assertEqual(len(ledger.list_trades()), 0)
            self.assertEqual(len(ledger.list_positions()), 0)
            self.assertEqual(len(ledger.list_pnl()), 0)
            self.assertEqual(ledger.get_setting("walletAddress"), "wallet-1")


if __name__ == "__main__":
    unittest.main()
