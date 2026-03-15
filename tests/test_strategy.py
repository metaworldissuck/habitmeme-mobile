from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.auto_engine import AutoEngine
from backend.bgw_client import RateLimitedError
from backend.config import Settings
from backend.ledger import Ledger
from backend.legacy_strategy import analyze_candidate
from backend.runner import Runner
from backend.strategy import StrategyDefaults, StrategyEngine


class DummyClient:
    pass


class DummyStrategy(StrategyEngine):
    def __init__(self) -> None:
        super().__init__(
            Runner(DummyClient()),  # type: ignore[arg-type]
            StrategyDefaults(
                min_liquidity_usd=60_000,
                stop_loss_pct=0.12,
                take_profit_cost_basis_pct=0.45,
                take_profit_half_pct=0.9,
                moonbag_trigger_pct=1.8,
                moonbag_fraction=0.1,
                max_hold_hours=18,
                time_exit_max_gain_pct=0.1,
            ),
        )


class StrategyTests(unittest.TestCase):
    def test_conservative_profile_requires_stronger_candidate(self) -> None:
        defaults = StrategyDefaults(
            min_liquidity_usd=60_000,
            stop_loss_pct=0.12,
            take_profit_cost_basis_pct=0.45,
            take_profit_half_pct=0.9,
            moonbag_trigger_pct=1.8,
            moonbag_fraction=0.1,
            max_hold_hours=18,
            time_exit_max_gain_pct=0.1,
        )
        conservative = defaults.profile("conservative")
        normal = defaults.profile("normal")
        self.assertGreater(conservative.min_liquidity_usd, normal.min_liquidity_usd)
        self.assertEqual(conservative.min_source_count, 2)
        self.assertLess(conservative.slot_budget_fraction, normal.slot_budget_fraction)

    def test_exit_signal_uses_two_x_cost_basis_recovery(self) -> None:
        strategy = DummyStrategy()
        signal = strategy.exit_signal_for_position(
            {
                "status": "open",
                "entry_price_sol": 1.0,
                "current_price_sol": 2.0,
                "take_profit_stage": "entry",
                "opened_at": "2026-03-14T00:00:00+00:00",
            },
            risk_mode="normal",
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal["reason"], "recover_cost_basis")

    def test_conservative_analysis_blocks_single_source_candidate(self) -> None:
        defaults = StrategyDefaults(
            min_liquidity_usd=60_000,
            stop_loss_pct=0.12,
            take_profit_cost_basis_pct=0.45,
            take_profit_half_pct=0.9,
            moonbag_trigger_pct=1.8,
            moonbag_fraction=0.1,
            max_hold_hours=18,
            time_exit_max_gain_pct=0.1,
        )
        profile = defaults.profile("conservative")
        analysis = analyze_candidate(
            {
                "symbol": "TEST",
                "name": "Test Meme",
                "sources": ["Hotpicks"],
                "source_ranks": {"Hotpicks": 1},
                "turnover_24h": 500_000,
            },
            token_info={
                "data": {
                    "holders": 600,
                    "twitter": "x",
                    "telegram": "tg",
                    "website": "web",
                    "top10_holder_percent": 20,
                    "insider_holder_percent": 4,
                    "sniper_holder_percent": 3,
                    "dev_holder_percent": 1,
                    "dev_rug_percent": 0,
                    "lock_lp_percent": 80,
                }
            },
            security={"data": {"highRisk": False, "cannotSellAll": False, "riskCount": 0, "warnCount": 0, "buyTax": 0, "sellTax": 0, "freezeAuth": False, "mintAuth": False, "lpLock": True}},
            liquidity={"data": {"liquidityUsd": 150_000}},
            tx_info={"data": {"buyVolume24h": 100_000, "sellVolume24h": 20_000, "buyers24h": 80, "sellers24h": 30, "buyVolume5m": 8_000, "sellVolume5m": 2_000}},
            rules=profile.rules(),
        )
        self.assertIn("sources<2", analysis["blocked_reasons"])

    def test_auto_slot_budget_respects_remaining_cap(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            budget = engine._slot_budget_sol(strategy.defaults.profile("normal"), [])
            self.assertAlmostEqual(budget, 0.025, places=9)
            budget_after_one_slot = engine._slot_budget_sol(
                strategy.defaults.profile("normal"),
                [{"cost_basis_sol": 0.025}],
            )
            self.assertAlmostEqual(budget_after_one_slot, 0.005, places=9)

    def test_auto_status_exposes_slot_and_reserve_budget_context(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            ledger.upsert_position(
                {
                    "token_contract": "token-1",
                    "token_symbol": "ONE",
                    "entry_price_sol": 0.001,
                    "current_price_sol": 0.001,
                    "amount": 10.0,
                    "market_value_sol": 0.025,
                    "cost_basis_sol": 0.025,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": 0.001,
                    "take_profit_stage": "entry",
                    "mode": "auto_live",
                    "opened_at": "2026-03-14T00:00:00+00:00",
                    "status": "open",
                    "updated_at": "2026-03-14T00:00:00+00:00",
                }
            )
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            engine.thread = Mock(is_alive=Mock(return_value=True))
            status = engine.status()
            self.assertEqual(status["slotsUsed"], 1)
            self.assertEqual(status["slotsMax"], 2)
            self.assertAlmostEqual(status["reserveSolBalance"], 0.02, places=9)
            self.assertAlmostEqual(status["deployableBudgetSol"], 0.03, places=9)
            self.assertAlmostEqual(status["deployedBudgetSol"], 0.025, places=9)
            self.assertAlmostEqual(status["availableBudgetSol"], 0.005, places=9)
            self.assertAlmostEqual(status["nextSlotBudgetSol"], 0.005, places=9)
            self.assertEqual(status["strategyProfile"]["riskMode"], "normal")
            self.assertAlmostEqual(status["strategyProfile"]["baseMinLiquidityUsd"], 60_000.0, places=9)
            self.assertAlmostEqual(status["strategyProfile"]["minLiquidityUsd"], 60_000.0, places=9)
            self.assertEqual(status["strategyProfile"]["maxOpenPositions"], 2)
            self.assertAlmostEqual(status["strategyProfile"]["slotBudgetFraction"], 0.5, places=9)
            self.assertAlmostEqual(status["strategyProfile"]["stopLossPct"], 0.12, places=9)
            self.assertEqual(status["nextStep"], "monitoring_existing_positions")
            self.assertEqual(len(status["slotPositions"]), 2)
            self.assertEqual(status["slotPositions"][0]["state"], "open")
            self.assertEqual(status["slotPositions"][0]["tokenSymbol"], "ONE")
            self.assertEqual(status["slotPositions"][1]["state"], "empty")

    def test_auto_status_reports_waiting_for_budget_release(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.02, risk_mode="normal")
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.02, "risk_mode": "normal"}
            engine.thread = Mock(is_alive=Mock(return_value=True))
            status = engine.status()
            self.assertEqual(status["nextStep"], "waiting_for_budget_release")
            self.assertIn("budget", status["nextStepMessage"].lower())

    def test_buy_guard_blocks_new_buys_without_stopping_auto(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            ledger.add_pnl_snapshot("auto_live", realized=-0.05, unrealized=0.0, open_positions=0)
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            engine.thread = Mock(is_alive=Mock(return_value=True))
            with patch.object(strategy, "discover") as discover_mock:
                engine._run_cycle()
            discover_mock.assert_not_called()
            status = engine.status()
            self.assertTrue(status["running"])
            self.assertEqual(status["nextStep"], "buy_guard_daily_loss")

    def test_auto_status_reports_cooldown_state(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            engine._enter_cooldown("rate_limited", 45.0)
            status = engine.status()
            self.assertTrue(status["cooldownActive"])
            self.assertEqual(status["cooldownReason"], "rate_limited")
            self.assertEqual(status["nextStep"], "cooldown_active")
            self.assertIn("retry", status["nextStepMessage"].lower())

    def test_auto_status_clears_stale_running_flag_when_thread_is_dead(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            status = engine.status()
            self.assertFalse(status["running"])
            self.assertFalse(status["runtimeAlive"])
            self.assertEqual(status["pausedReason"], "runtime_stopped")

    def test_quote_throttle_waits_for_min_interval(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.last_quote_attempt_at = 100.0
            with patch("backend.auto_engine.time.monotonic", side_effect=[105.0, 112.0]):
                with patch.object(engine.stop_event, "wait") as wait_mock:
                    engine._respect_quote_throttle("buy", "TEST")
            wait_mock.assert_called_once()
            self.assertGreater(wait_mock.call_args.args[0], 0.0)

    def test_auto_recovers_prepared_order_by_resubmitting(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.create_order(
                {
                    "order_id": "order-1",
                    "client_trade_id": "auto-test-1",
                    "side": "buy",
                    "token_symbol": "TOK",
                    "token_contract": "contract-1",
                    "mode": "auto_live",
                    "status": "prepared",
                    "market": "mock-market",
                    "amount_in_sol": 0.02,
                    "amount_out": 100.0,
                    "raw_order_json": {"data": {"orderId": "order-1"}},
                    "created_at": "2026-03-14T00:00:00+00:00",
                    "updated_at": "2026-03-14T00:00:00+00:00",
                }
            )
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            with patch.object(engine.runner, "sign_order", return_value=["signed"]) as sign_mock:
                with patch.object(engine.runner, "order_submit", return_value={"status": 0, "data": {"ok": True}}) as submit_mock:
                    with patch.object(engine.runner, "order_status", return_value={"data": {"status": "success"}, "txs": [{"txId": "tx-1"}]}):
                        engine._recover_pending_order(ledger.get_active_order())
            sign_mock.assert_called_once()
            submit_mock.assert_called_once_with("order-1", ["signed"])
            order = ledger.get_order(order_id="order-1")
            self.assertEqual(order["status"], "success")

    def test_auto_sell_updates_existing_position_instead_of_inserting_history_duplicate(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
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
                    "opened_at": "2026-03-14T00:00:00+00:00",
                    "status": "open",
                    "updated_at": "2026-03-14T00:00:00+00:00",
                }
            )
            position = ledger.latest_open_position("contract-1")
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            with patch.object(engine, "_quote_with_retry", return_value={"status": 0, "data": {}}):
                with patch.object(strategy, "summarize_quote", return_value={"market": "mock-market", "toAmount": 0.11}):
                    with patch.object(engine.runner, "order_create", return_value={"data": {"orderId": "sell-1"}}):
                        with patch.object(engine.runner, "sign_order", return_value=["signed"]):
                            with patch.object(engine.runner, "order_submit", return_value={"status": 0, "data": {"ok": True}}):
                                with patch.object(engine, "_poll_status", return_value={"data": {"status": "success", "receiveAmount": 0.11}, "txs": [{"txId": "tx-1"}]}):
                                    engine._execute_sell(position, {"reason": "manual_test", "fraction": 1.0, "next_stage": "moonbag"})
            rows = ledger.list_positions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "closed")
            self.assertEqual(rows[0]["id"], position["id"])

    def test_discover_retries_once_before_skipping_cycle(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            expected = {"analyses": [], "recommended": None, "rankingType": "combined", "riskMode": "normal"}
            with patch.object(strategy, "discover", side_effect=[RateLimitedError("limited"), expected]) as discover_mock:
                with patch.object(engine.stop_event, "wait") as wait_mock:
                    result = engine._discover_with_retry(
                        ranking_type="combined",
                        risk_mode="normal",
                        excluded_contracts=set(),
                    )
            self.assertEqual(result, expected)
            self.assertEqual(discover_mock.call_count, 2)
            wait_mock.assert_called_once()

    def test_quote_retries_once_before_raising(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            with patch.object(engine.runner, "order_quote", side_effect=[RateLimitedError("limited"), {"status": 0, "data": {}}]) as quote_mock:
                with patch.object(engine.stop_event, "wait") as wait_mock:
                    result = engine._quote_with_retry(
                        side="buy",
                        token_symbol="TEST",
                        from_chain="sol",
                        from_contract="",
                        to_chain="sol",
                        to_contract="contract",
                        amount="0.01000000",
                        from_address="wallet",
                    )
            self.assertEqual(result, {"status": 0, "data": {}})
            self.assertEqual(quote_mock.call_count, 2)
            wait_mock.assert_called_once()

    def test_auto_status_exposes_decision_snapshot(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            ledger.start_auto_run(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="wallet",
                private_key_sol="secret",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            engine.state = {"ranking_type": "combined", "budget_sol": 0.05, "risk_mode": "normal"}
            engine.last_discovery = {
                "rankingType": "combined",
                "riskMode": "normal",
                "selected": {
                    "symbol": "WIN",
                    "name": "Winner",
                    "contract": "winner-contract",
                    "score": 12.5,
                    "narrativeScore": 5.5,
                    "communityScore": 4.0,
                    "smartMoneyScore": 3.0,
                    "sources": ["Hotpicks", "topGainers"],
                    "blockedReasons": [],
                    "warnings": ["liquidity.thin"],
                    "whyChosen": ["Multi-ranking candidate", "Strong liquidity"],
                    "whyBlocked": [],
                    "selected": True,
                },
                "shortlisted": [],
                "blocked": [
                    {
                        "symbol": "NOPE",
                        "name": "Blocked Token",
                        "contract": "blocked-contract",
                        "score": -88.0,
                        "narrativeScore": 1.0,
                        "communityScore": 0.5,
                        "smartMoneyScore": -2.0,
                        "sources": ["Hotpicks"],
                        "blockedReasons": ["security.highRisk"],
                        "warnings": [],
                        "whyChosen": ["Best remaining score"],
                        "whyBlocked": ["security.highRisk"],
                        "selected": False,
                    }
                ],
            }
            status = engine.status()
            self.assertEqual(status["decisionSnapshot"]["selected"]["symbol"], "WIN")
            self.assertEqual(status["decisionSnapshot"]["selected"]["narrativeScore"], 5.5)
            self.assertEqual(status["decisionSnapshot"]["selected"]["communityScore"], 4.0)
            self.assertEqual(status["decisionSnapshot"]["selected"]["smartMoneyScore"], 3.0)
            self.assertEqual(status["decisionSnapshot"]["blocked"][0]["whyBlocked"][0], "security.highRisk")

    def test_auto_engine_refreshes_runtime_credentials(self) -> None:
        strategy = DummyStrategy()
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(db_path=Path(tmpdir) / "habitmeme.db", legacy_root=Path(tmpdir) / "missing")
            ledger = Ledger(settings)
            ledger.initialize()
            engine = AutoEngine(
                ledger=ledger,
                runner=Runner(DummyClient()),  # type: ignore[arg-type]
                strategy=strategy,
                default_wallet="",
                private_key_sol="",
                discover_interval=90,
                poll_interval=1,
                poll_max=2,
                reserve_sol_balance=0.02,
                daily_loss_limit_sol=0.03,
                max_consecutive_losses=2,
            )
            with self.assertRaisesRegex(Exception, "HMS_SOL_ADDRESS"):
                engine.start(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            engine.refresh_credentials(default_wallet="wallet", private_key_sol="secret")
            status = engine.start(ranking_type="combined", budget_sol=0.05, risk_mode="normal")
            self.assertEqual(status["running"], 1)
            engine.stop()


if __name__ == "__main__":
    unittest.main()
