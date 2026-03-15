from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

from .bgw_client import CircuitOpenError, RateLimitedError, UpstreamRequestError
from .ledger import Ledger, as_float, now_iso
from .runner import Runner, ServiceError, extract_tx_id
from .strategy import StrategyEngine, determine_quote_feature

logger = logging.getLogger("habitmeme.auto")


class AutoEngine:
    RATE_LIMIT_COOLDOWN_SECONDS = 45.0
    CIRCUIT_COOLDOWN_SECONDS = 30.0
    UPSTREAM_ERROR_COOLDOWN_SECONDS = 20.0
    RUNTIME_ERROR_COOLDOWN_SECONDS = 20.0
    QUOTE_MIN_INTERVAL_SECONDS = 12.0
    DISCOVER_RETRY_DELAY_SECONDS = 2.5
    QUOTE_RETRY_DELAY_SECONDS = 2.5

    def __init__(
        self,
        *,
        ledger: Ledger,
        runner: Runner,
        strategy: StrategyEngine,
        default_wallet: str,
        private_key_sol: str,
        discover_interval: int,
        poll_interval: int,
        poll_max: int,
        reserve_sol_balance: float,
        daily_loss_limit_sol: float,
        max_consecutive_losses: int,
    ) -> None:
        self.ledger = ledger
        self.runner = runner
        self.strategy = strategy
        self.default_wallet = default_wallet
        self.private_key_sol = private_key_sol
        self.discover_interval = discover_interval
        self.poll_interval = poll_interval
        self.poll_max = poll_max
        self.reserve_sol_balance = reserve_sol_balance
        self.daily_loss_limit_sol = daily_loss_limit_sol
        self.max_consecutive_losses = max_consecutive_losses
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.execution_lock = threading.Lock()
        self.state: dict[str, Any] = {}
        self.last_discovery: dict[str, Any] | None = None
        self.cooldown_until = 0.0
        self.cooldown_reason = ""
        self.last_quote_attempt_at = 0.0

    def start(self, *, ranking_type: str, budget_sol: float, risk_mode: str) -> dict[str, Any]:
        missing = []
        if not self.default_wallet:
            missing.append("HMS_SOL_ADDRESS")
        if not self.private_key_sol:
            missing.append("HMS_SOL_PRIVATE_KEY")
        if missing:
            logger.warning("auto start blocked missing_credentials=%s", ",".join(missing))
            raise ServiceError(f"auto_live missing required runtime credentials: {', '.join(missing)}")
        if self.thread and self.thread.is_alive():
            logger.info("auto start ignored because thread is already running")
            return self.status()
        self.stop_event.clear()
        self.state = {"ranking_type": ranking_type, "budget_sol": budget_sol, "risk_mode": risk_mode}
        self.last_discovery = None
        logger.info("auto start ranking_type=%s risk_mode=%s budget_sol=%.6f wallet=%s", ranking_type, risk_mode, budget_sol, self.default_wallet)
        self.ledger.start_auto_run(ranking_type=ranking_type, budget_sol=budget_sol, risk_mode=risk_mode)
        self.thread = threading.Thread(target=self._run_loop, name="auto-engine", daemon=True)
        self.thread.start()
        return self.status()

    def refresh_credentials(self, *, default_wallet: str, private_key_sol: str) -> None:
        self.default_wallet = default_wallet.strip()
        self.private_key_sol = private_key_sol.strip()

    def stop(self, reason: str = "manual_stop") -> dict[str, Any]:
        logger.info("auto stop reason=%s", reason)
        self.stop_event.set()
        self.ledger.stop_auto_run(reason)
        return self.status()

    def status(self) -> dict[str, Any]:
        base_status = self.ledger.get_auto_status()
        runtime_alive = bool(self.thread and self.thread.is_alive())
        if base_status.get("running") and not runtime_alive:
            self.ledger.update_auto_run(running=0, paused_reason="runtime_stopped", last_action="runtime_stopped")
            base_status = self.ledger.get_auto_status()
        risk_mode = str(base_status.get("riskMode") or self.state.get("risk_mode") or "normal")
        profile = self.strategy.defaults.profile(risk_mode)
        open_positions = self.ledger.open_positions()
        budget_context = self._budget_context(profile, open_positions, total_budget=as_float(base_status.get("budgetSol")))
        cooldown_active = self._cooldown_active(update_ledger=False)
        return {
            **base_status,
            "running": runtime_alive,
            "runtimeAlive": runtime_alive,
            **budget_context,
            "strategyProfile": self._profile_snapshot(profile),
            "slotPositions": self._slot_positions(profile, open_positions),
            "decisionSnapshot": self.last_discovery or {},
            "cooldownActive": cooldown_active,
            "cooldownReason": self.cooldown_reason if cooldown_active else "",
            "cooldownRemainingSec": max(self.cooldown_until - time.monotonic(), 0.0) if cooldown_active else 0.0,
            **self._operational_state(base_status, open_positions, budget_context),
        }

    def _run_loop(self) -> None:
        logger.info("auto loop entered")
        while not self.stop_event.is_set():
            try:
                if self._cooldown_active():
                    wait = max(self.cooldown_until - time.monotonic(), 0.0)
                    logger.info("auto cooldown active reason=%s wait=%.2fs", self.cooldown_reason or "rate_limited", wait)
                    self.ledger.update_auto_run(running=1, paused_reason=self.cooldown_reason, last_action="cooldown_active")
                    self.stop_event.wait(min(wait, max(float(self.discover_interval), 1.0)))
                    continue
                with self.execution_lock:
                    self._run_cycle()
            except RateLimitedError:
                logger.warning("auto entering cooldown by rate limit protection")
                self.ledger.record_risk_event("rate_limited", "Auto paused after repeated 429")
                self._enter_cooldown("rate_limited", self.RATE_LIMIT_COOLDOWN_SECONDS)
                continue
            except CircuitOpenError:
                logger.warning("auto entering cooldown by circuit breaker")
                self.ledger.record_risk_event("network_unstable", "Circuit breaker opened")
                self._enter_cooldown("network_unstable", self.CIRCUIT_COOLDOWN_SECONDS)
                continue
            except UpstreamRequestError as exc:
                logger.warning("auto entering cooldown by upstream error detail=%s", str(exc))
                self.ledger.record_risk_event("api_errors_burst", str(exc))
                self._enter_cooldown("api_errors_burst", self.UPSTREAM_ERROR_COOLDOWN_SECONDS)
                continue
            except Exception as exc:
                logger.exception("auto crashed")
                self.ledger.record_risk_event("auto_engine_error", str(exc))
                self._enter_cooldown("api_errors_burst", self.RUNTIME_ERROR_COOLDOWN_SECONDS)
                continue
            self.stop_event.wait(self.discover_interval)

    def _buy_guard_reason(self) -> str | None:
        if self.ledger.today_pnl() <= -self.daily_loss_limit_sol:
            return "daily_loss_limit_guard"
        if self.ledger.count_recent_losses() >= self.max_consecutive_losses:
            return "max_consecutive_losses_guard"
        return None

    def _run_cycle(self) -> None:
        logger.info("auto cycle start ranking_type=%s risk_mode=%s", self.state.get("ranking_type"), self.state.get("risk_mode"))
        active_order = self.ledger.get_active_order()
        if active_order:
            logger.info("auto recovering pending order order_id=%s status=%s", active_order.get("order_id"), active_order.get("status"))
            self._recover_pending_order(active_order)
            return
        profile = self.strategy.defaults.profile(self.state["risk_mode"])
        open_positions = self.ledger.open_positions()
        if open_positions and self._manage_positions(open_positions):
            return
        open_positions = self.ledger.open_positions()
        if len(open_positions) >= profile.max_open_positions:
            logger.info("auto waiting: slot limit reached slots=%s max=%s", len(open_positions), profile.max_open_positions)
            self.ledger.update_auto_run(last_action="slot_limit_reached")
            return
        buy_guard_reason = self._buy_guard_reason()
        if buy_guard_reason:
            logger.info("auto waiting: buy guard active reason=%s", buy_guard_reason)
            self.ledger.update_auto_run(running=1, paused_reason="", last_action=buy_guard_reason)
            return
        budget_sol = self._slot_budget_sol(profile, open_positions)
        if budget_sol <= 0:
            budget_context = self._budget_context(profile, open_positions, total_budget=as_float(self.state.get("budget_sol")))
            exhausted_reason = "reserve_floor_reached" if budget_context["availableBudgetSol"] <= 0 else "budget_exhausted"
            logger.info(
                "auto waiting: budget unavailable reason=%s total=%.6f reserve=%.6f available=%.6f next_slot=%.6f",
                exhausted_reason,
                as_float(self.state.get("budget_sol")),
                budget_context["reserveSolBalance"],
                budget_context["availableBudgetSol"],
                budget_context["nextSlotBudgetSol"],
            )
            self.ledger.update_auto_run(last_action=exhausted_reason)
            return
        logger.info("auto discover start ranking_type=%s risk_mode=%s budget_sol=%.6f", self.state["ranking_type"], self.state["risk_mode"], budget_sol)
        discovery = self._discover_with_retry(
            ranking_type=self.state["ranking_type"],
            risk_mode=self.state["risk_mode"],
            excluded_contracts={str(position["token_contract"]) for position in open_positions},
        )
        if discovery is None:
            return
        self.last_discovery = self._decision_snapshot(discovery)
        logger.info(
            "auto discover done candidates=%s shortlisted=%s blocked=%s selected=%s",
            len(discovery.get("analyses") or []),
            len((self.last_discovery or {}).get("shortlisted") or []),
            len((self.last_discovery or {}).get("blocked") or []),
            ((self.last_discovery or {}).get("selected") or {}).get("symbol", "-"),
        )
        recommended = discovery["recommended"]
        if not recommended:
            logger.info("auto no eligible candidate found")
            self.ledger.update_auto_run(last_action="discover_no_candidate")
            return
        if recommended["blocked_reasons"]:
            logger.info("auto candidate blocked symbol=%s reasons=%s", recommended["candidate"].get("symbol", "-"), ",".join(recommended["blocked_reasons"]))
            self.ledger.record_risk_event("blocked_candidate", ",".join(recommended["blocked_reasons"]))
            self.ledger.update_auto_run(last_action="candidate_blocked")
            return
        logger.info(
            "auto selected candidate symbol=%s contract=%s score=%.2f",
            recommended["candidate"].get("symbol", "-"),
            recommended["candidate"].get("contract", "-"),
            as_float(recommended.get("score")),
        )
        self._execute_buy(recommended, budget_sol=budget_sol)

    def _recover_pending_order(self, active_order: dict[str, Any]) -> None:
        order_id = active_order.get("order_id")
        if not order_id:
            self.ledger.update_order(active_order["client_trade_id"], status="failed", error_reason="missing_order_id")
            return
        status = str(active_order.get("status") or "")
        if status == "prepared":
            raw_order = active_order.get("raw_order_json")
            if not raw_order:
                self.ledger.update_order(active_order["client_trade_id"], status="failed", error_reason="missing_raw_order")
                return
            try:
                order_response = raw_order if isinstance(raw_order, dict) else json.loads(raw_order)
            except Exception:
                self.ledger.update_order(active_order["client_trade_id"], status="failed", error_reason="invalid_raw_order")
                return
            logger.info("auto recovering prepared order by resubmitting order_id=%s side=%s", order_id, active_order.get("side", "-"))
            signed = self.runner.sign_order(order_response, private_key_sol=self.private_key_sol)
            self.runner.order_submit(str(order_id), signed)
            self.ledger.update_order(active_order["client_trade_id"], status="submitted")
        status_payload = self.runner.order_status(str(order_id))
        status_item = status_payload.get("data", status_payload)
        status = str(status_item.get("status", "unknown"))
        tx_id = extract_tx_id(status_payload)
        self.ledger.update_order(active_order["client_trade_id"], status=status, tx_id=tx_id)
        self.ledger.update_auto_run(last_action=f"recovered_{status}", last_order_id=str(order_id), last_tx_id=tx_id)

    def _execute_buy(self, analysis: dict[str, Any], *, budget_sol: float) -> None:
        token_contract = str(analysis["candidate"].get("contract", ""))
        token_symbol = str(analysis["candidate"].get("symbol", ""))
        client_trade_id = f"auto-{uuid.uuid4().hex[:12]}"
        self._respect_quote_throttle("buy", token_symbol)
        logger.info("auto buy quote symbol=%s contract=%s budget_sol=%.6f", token_symbol, token_contract, budget_sol)
        quote = self._quote_with_retry(
            side="buy",
            token_symbol=token_symbol,
            from_chain="sol",
            from_contract="",
            to_chain="sol",
            to_contract=token_contract,
            amount=f"{budget_sol:.8f}",
            from_address=self.default_wallet,
        )
        quote_item = self.strategy.summarize_quote(quote)
        market = quote_item["market"]
        if not market:
            logger.warning("auto buy aborted missing market symbol=%s", token_symbol)
            self.ledger.record_risk_event("quote_failed", token_symbol)
            return
        feature = determine_quote_feature(quote, budget_sol)
        logger.info("auto buy create order symbol=%s market=%s feature=%s", token_symbol, market, feature)
        order = self.runner.order_create(
            from_chain="sol",
            from_contract="",
            to_chain="sol",
            to_contract=token_contract,
            amount=f"{budget_sol:.8f}",
            from_address=self.default_wallet,
            market=market,
            feature=feature,
        )
        order_data = self._require_order_data(order, "order_create")
        order_id = str(order_data.get("orderId", ""))
        self.ledger.create_order(
            {
                "order_id": order_id,
                "client_trade_id": client_trade_id,
                "side": "buy",
                "token_symbol": token_symbol,
                "token_contract": token_contract,
                "mode": "auto_live",
                "status": "prepared",
                "market": market,
                "amount_in_sol": budget_sol,
                "amount_out": quote_item["toAmount"],
                "raw_order_json": order,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        signed = self.runner.sign_order(order, private_key_sol=self.private_key_sol)
        logger.info("auto buy submit order symbol=%s order_id=%s", token_symbol, order_id)
        submit = self.runner.order_submit(order_id, signed)
        submit_data = submit.get("data", submit)
        self.ledger.update_order(client_trade_id, status="submitted")
        self.ledger.add_trade(
            {
                "client_trade_id": client_trade_id,
                "order_id": order_id,
                "side": "buy",
                "token_symbol": token_symbol,
                "token_contract": token_contract,
                "amount_in_sol": budget_sol,
                "amount_out": quote_item["toAmount"],
                "mode": "auto_live",
                "status": "submitted",
                "note": "auto buy",
            }
        )
        self.ledger.update_auto_run(last_action="submitted_buy", last_order_id=order_id)
        final_status = self._poll_status(order_id)
        final_item = final_status.get("data", final_status)
        tx_id = extract_tx_id(final_status)
        logger.info("auto buy final status symbol=%s order_id=%s status=%s tx_id=%s", token_symbol, order_id, final_item.get("status", "unknown"), tx_id)
        current_price_sol, _ = self.strategy.current_price_in_sol(token_contract)
        receive_amount = self._resolve_buy_amount(
            quoted_amount=as_float(final_item.get("receiveAmount") or final_item.get("toAmount")),
            spend_amount=budget_sol,
            token_contract=token_contract,
            current_price_sol=current_price_sol,
        )
        self.ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id, amount_out=receive_amount)
        self.ledger.add_trade(
            {
                "client_trade_id": client_trade_id,
                "order_id": order_id,
                "side": "buy",
                "token_symbol": token_symbol,
                "token_contract": token_contract,
                "amount_in_sol": budget_sol,
                "amount_out": receive_amount,
                "mode": "auto_live",
                "status": final_item.get("status", "unknown"),
                "tx_id": tx_id,
                "note": "auto buy final",
            }
        )
        if final_item.get("status") == "success" and receive_amount > 0:
            entry_price_sol = budget_sol / receive_amount
            self.ledger.upsert_position(
                {
                    "token_contract": token_contract,
                    "token_symbol": token_symbol,
                    "entry_price_sol": entry_price_sol,
                    "current_price_sol": current_price_sol,
                    "amount": receive_amount,
                    "market_value_sol": receive_amount * current_price_sol,
                    "cost_basis_sol": budget_sol,
                    "realized_pnl_sol": 0.0,
                    "peak_price_sol": current_price_sol,
                    "take_profit_stage": "entry",
                    "mode": "auto_live",
                    "opened_at": now_iso(),
                    "status": "open",
                    "updated_at": now_iso(),
                }
            )
            self.ledger.add_pnl_snapshot(
                "auto_live",
                realized=0.0,
                unrealized=(receive_amount * current_price_sol) - budget_sol,
                open_positions=1,
                token_symbol=token_symbol,
                token_contract=token_contract,
            )
            self.ledger.update_auto_run(last_action="buy_success", last_order_id=order_id, last_tx_id=tx_id)

    def _manage_positions(self, positions: list[dict[str, Any]]) -> bool:
        logger.info("auto managing positions count=%s", len(positions))
        for position in positions:
            current_price_sol, _ = self.strategy.current_price_in_sol(position["token_contract"])
            amount = as_float(position["amount"])
            self.ledger.upsert_position(
                {
                    **position,
                    "current_price_sol": current_price_sol,
                    "market_value_sol": amount * current_price_sol,
                    "peak_price_sol": max(as_float(position.get("peak_price_sol")), current_price_sol),
                    "updated_at": now_iso(),
                }
            )
            signal = self.strategy.exit_signal_for_position(
                {**position, "current_price_sol": current_price_sol},
                risk_mode=self.state["risk_mode"],
            )
            if signal:
                logger.info(
                    "auto exit signal symbol=%s reason=%s fraction=%.4f",
                    position.get("token_symbol", "-"),
                    signal.get("reason", "-"),
                    as_float(signal.get("fraction"), 0.0),
                )
                self._execute_sell(position, signal)
                return True
        unrealized = sum(as_float(item["market_value_sol"]) - as_float(item["cost_basis_sol"]) for item in self.ledger.open_positions())
        latest_realized = self.ledger.latest_pnl() or {}
        focus_position = self.ledger.open_positions()[0] if self.ledger.open_positions() else {}
        self.ledger.add_pnl_snapshot(
            "auto_live",
            realized=as_float(latest_realized.get("realized")),
            unrealized=unrealized,
            open_positions=len(self.ledger.open_positions()),
            token_symbol=str(focus_position.get("token_symbol", "")),
            token_contract=str(focus_position.get("token_contract", "")),
        )
        self.ledger.update_auto_run(last_action="positions_refreshed")
        return False

    def _execute_sell(self, position: dict[str, Any], signal: dict[str, Any]) -> None:
        token_contract = str(position["token_contract"])
        token_symbol = str(position["token_symbol"])
        token_amount = as_float(position["amount"]) * float(signal["fraction"])
        client_trade_id = f"auto-{uuid.uuid4().hex[:12]}"
        self._respect_quote_throttle("sell", token_symbol)
        logger.info("auto sell quote symbol=%s contract=%s amount=%.8f reason=%s", token_symbol, token_contract, token_amount, signal.get("reason", "-"))
        quote = self._quote_with_retry(
            side="sell",
            token_symbol=token_symbol,
            from_chain="sol",
            from_contract=token_contract,
            to_chain="sol",
            to_contract="",
            amount=f"{token_amount:.8f}",
            from_address=self.default_wallet,
        )
        quote_summary = self.strategy.summarize_quote(quote)
        market = quote_summary["market"]
        if not market:
            logger.warning("auto sell aborted missing market symbol=%s", token_symbol)
            return
        logger.info("auto sell create order symbol=%s market=%s", token_symbol, market)
        order = self.runner.order_create(
            from_chain="sol",
            from_contract=token_contract,
            to_chain="sol",
            to_contract="",
            amount=f"{token_amount:.8f}",
            from_address=self.default_wallet,
            market=market,
        )
        order_data = self._require_order_data(order, "order_create")
        order_id = str(order_data.get("orderId", ""))
        self.ledger.create_order(
            {
                "order_id": order_id,
                "client_trade_id": client_trade_id,
                "position_id": int(position["id"]),
                "side": "sell",
                "token_symbol": token_symbol,
                "token_contract": token_contract,
                "mode": "auto_live",
                "status": "prepared",
                "market": market,
                "amount_in_sol": 0.0,
                "amount_out": quote_summary["toAmount"],
                "raw_order_json": order,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        signed = self.runner.sign_order(order, private_key_sol=self.private_key_sol)
        logger.info("auto sell submit order symbol=%s order_id=%s", token_symbol, order_id)
        self.runner.order_submit(order_id, signed)
        self.ledger.update_order(client_trade_id, status="submitted")
        final_status = self._poll_status(order_id)
        final_item = final_status.get("data", final_status)
        tx_id = extract_tx_id(final_status)
        logger.info("auto sell final status symbol=%s order_id=%s status=%s tx_id=%s", token_symbol, order_id, final_item.get("status", "unknown"), tx_id)
        current_price_sol = as_float(position.get("current_price_sol"))
        if current_price_sol <= 0:
            current_price_sol, _ = self.strategy.current_price_in_sol(token_contract)
        receive_amount = as_float(final_item.get("receiveAmount") or final_item.get("toAmount"))
        if receive_amount <= 0:
            receive_amount = token_amount * current_price_sol
        self.ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id, amount_out=receive_amount)
        self.ledger.add_trade(
            {
                "client_trade_id": client_trade_id,
                "order_id": order_id,
                "position_id": int(position["id"]),
                "side": "sell",
                "token_symbol": token_symbol,
                "token_contract": token_contract,
                "amount_in_sol": 0.0,
                "amount_out": receive_amount,
                "mode": "auto_live",
                "status": final_item.get("status", "unknown"),
                "tx_id": tx_id,
                "note": signal["reason"],
            }
        )
        if final_item.get("status") == "success":
            entry_price_sol = as_float(position.get("entry_price_sol"))
            original_amount = as_float(position.get("amount"))
            remaining_amount = max(original_amount - token_amount, 0.0)
            realized_pnl = receive_amount - (token_amount * entry_price_sol)
            cumulative_realized = as_float(position.get("realized_pnl_sol")) + realized_pnl
            is_closed = remaining_amount <= max(original_amount * 0.0001, 1e-9)
            self.ledger.upsert_position(
                {
                    "token_contract": token_contract,
                    "token_symbol": token_symbol,
                    "entry_price_sol": entry_price_sol,
                    "current_price_sol": receive_amount / token_amount if token_amount else 0.0,
                    "amount": 0.0 if is_closed else remaining_amount,
                    "market_value_sol": 0.0 if is_closed else remaining_amount * as_float(position.get("current_price_sol")),
                    "cost_basis_sol": 0.0 if is_closed else remaining_amount * entry_price_sol,
                    "realized_pnl_sol": cumulative_realized,
                    "peak_price_sol": max(as_float(position.get("peak_price_sol")), as_float(position.get("current_price_sol"))),
                    "take_profit_stage": signal.get("next_stage", position.get("take_profit_stage", "entry")),
                    "mode": "auto_live",
                    "opened_at": position.get("opened_at"),
                    "closed_at": now_iso() if is_closed else None,
                    "status": "closed" if is_closed else "open",
                    "updated_at": now_iso(),
                }
            )
            unrealized = sum(as_float(item["market_value_sol"]) - as_float(item["cost_basis_sol"]) for item in self.ledger.open_positions())
            self.ledger.add_pnl_snapshot(
                "auto_live",
                realized=cumulative_realized,
                unrealized=unrealized,
                open_positions=len(self.ledger.open_positions()),
                token_symbol=token_symbol,
                token_contract=token_contract,
            )
            self.ledger.update_auto_run(last_action=f"sell_{signal['reason']}", last_order_id=order_id, last_tx_id=tx_id)

    def _resolve_buy_amount(
        self,
        *,
        quoted_amount: float,
        spend_amount: float,
        token_contract: str,
        current_price_sol: float,
    ) -> float:
        if quoted_amount > 0:
            return quoted_amount
        spot_price = current_price_sol
        if spot_price <= 0:
            spot_price, _ = self.strategy.current_price_in_sol(token_contract)
        if spend_amount > 0 and spot_price > 0:
            return spend_amount / spot_price
        return 0.0

    def _slot_budget_sol(self, profile: Any, open_positions: list[dict[str, Any]]) -> float:
        total_budget = as_float(self.state.get("budget_sol"))
        return self._budget_context(profile, open_positions, total_budget=total_budget)["nextSlotBudgetSol"]

    def _budget_context(self, profile: Any, open_positions: list[dict[str, Any]], *, total_budget: float) -> dict[str, Any]:
        deployed = sum(as_float(position.get("cost_basis_sol")) for position in open_positions)
        reserve = max(self.reserve_sol_balance, 0.0)
        deployable_budget = max(total_budget - reserve, 0.0)
        available_budget = max(deployable_budget - deployed, 0.0)
        slot_cap = max(total_budget * float(profile.slot_budget_fraction), 0.0)
        next_slot_budget = min(available_budget, slot_cap)
        return {
            "slotsUsed": len(open_positions),
            "slotsMax": int(profile.max_open_positions),
            "reserveSolBalance": reserve,
            "deployableBudgetSol": deployable_budget,
            "deployedBudgetSol": deployed,
            "availableBudgetSol": available_budget,
            "nextSlotBudgetSol": next_slot_budget,
        }

    def _profile_snapshot(self, profile: Any) -> dict[str, Any]:
        return {
            "riskMode": profile.risk_mode,
            "baseMinLiquidityUsd": float(self.strategy.defaults.min_liquidity_usd),
            "minLiquidityUsd": float(profile.min_liquidity_usd),
            "maxOpenPositions": int(profile.max_open_positions),
            "slotBudgetFraction": float(profile.slot_budget_fraction),
            "stopLossPct": float(profile.stop_loss_pct),
            "takeProfitCostBasisPct": float(profile.take_profit_cost_basis_pct),
            "takeProfitHalfPct": float(profile.take_profit_half_pct),
            "moonbagTriggerPct": float(profile.moonbag_trigger_pct),
            "moonbagFraction": float(profile.moonbag_fraction),
            "maxHoldHours": float(profile.max_hold_hours),
            "timeExitMaxGainPct": float(profile.time_exit_max_gain_pct),
            "minHolders": int(profile.min_holders),
            "minSocialLinks": int(profile.min_social_links),
            "minSourceCount": int(profile.min_source_count),
            "maxTop10HolderPercent": float(profile.max_top10_holder_percent),
            "maxInsiderHolderPercent": float(profile.max_insider_holder_percent),
            "maxSniperHolderPercent": float(profile.max_sniper_holder_percent),
            "maxDevHolderPercent": float(profile.max_dev_holder_percent),
            "maxDevRugPercent": float(profile.max_dev_rug_percent),
        }

    def _operational_state(
        self,
        base_status: dict[str, Any],
        open_positions: list[dict[str, Any]],
        budget_context: dict[str, Any],
    ) -> dict[str, str]:
        paused_reason = str(base_status.get("pausedReason") or "")
        last_action = str(base_status.get("lastAction") or "")
        active_order = self.ledger.get_active_order()
        slots_used = len(open_positions)
        slots_max = int(budget_context.get("slotsMax") or 0)
        available_budget = as_float(budget_context.get("availableBudgetSol"))
        next_slot_budget = as_float(budget_context.get("nextSlotBudgetSol"))
        running = bool(base_status.get("running"))

        if self._cooldown_active(update_ledger=False):
            wait = max(self.cooldown_until - time.monotonic(), 0.0)
            reason = (self.cooldown_reason or "rate_limited").replace("_", " ")
            return {
                "nextStep": "cooldown_active",
                "nextStepMessage": f"Cooling down after {reason}. Auto will retry in about {int(round(wait))}s.",
            }
        if paused_reason == "rate_limited":
            return {"nextStep": "cooldown_active", "nextStepMessage": "Waiting for API rate limits to clear before opening new positions."}
        if paused_reason in {"network_unstable", "api_errors_burst"}:
            return {"nextStep": "cooldown_active", "nextStepMessage": "Waiting for upstream network stability before trading again."}
        if last_action == "daily_loss_limit_guard":
            return {"nextStep": "buy_guard_daily_loss", "nextStepMessage": "Daily loss guard is active. Auto will keep managing existing positions but will wait before opening a new one."}
        if last_action == "max_consecutive_losses_guard":
            return {"nextStep": "buy_guard_consecutive_losses", "nextStepMessage": "Consecutive-loss guard is active. Auto stays on and will keep managing positions, but new buys are temporarily blocked."}
        if active_order:
            return {"nextStep": "waiting_for_order_finality", "nextStepMessage": "Waiting for the current order to settle before taking the next action."}
        if not running:
            return {"nextStep": "idle", "nextStepMessage": "Auto is idle. Start auto to scan rankings and manage positions."}
        if slots_max > 0 and slots_used >= slots_max:
            return {"nextStep": "waiting_for_free_slot", "nextStepMessage": "All slots are occupied, so auto is waiting for a position to exit before opening a new one."}
        if available_budget <= 0 or next_slot_budget <= 0:
            return {"nextStep": "waiting_for_budget_release", "nextStepMessage": "Deployable budget is fully reserved or already used. Auto will wait for budget to free up."}
        if open_positions:
            return {"nextStep": "monitoring_existing_positions", "nextStepMessage": "Monitoring open positions for stop loss, profit taking, or time-based exits."}
        if last_action in {"discover_no_candidate", "candidate_blocked"}:
            return {"nextStep": "waiting_for_next_discovery", "nextStepMessage": "Waiting for the next discover cycle because the latest candidates were not tradeable."}
        if last_action in {"submitted_buy", "buy_success"} or last_action.startswith("recovered_"):
            return {"nextStep": "waiting_for_order_finality", "nextStepMessage": "Waiting for the most recent order lifecycle to finish syncing."}
        return {"nextStep": "scanning_rankings", "nextStepMessage": "Scanning official rankings for the next candidate that passes the current risk profile."}

    def _slot_positions(self, profile: Any, open_positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(open_positions, key=lambda item: str(item.get("opened_at") or item.get("updated_at") or ""))
        slots: list[dict[str, Any]] = []
        for index in range(int(profile.max_open_positions)):
            if index < len(ordered):
                position = ordered[index]
                market_value = as_float(position.get("market_value_sol"))
                cost_basis = as_float(position.get("cost_basis_sol"))
                slots.append(
                    {
                        "slot": index + 1,
                        "state": "open",
                        "tokenSymbol": position.get("token_symbol", ""),
                        "tokenContract": position.get("token_contract", ""),
                        "amount": as_float(position.get("amount")),
                        "costBasisSol": cost_basis,
                        "marketValueSol": market_value,
                        "unrealizedPnlSol": market_value - cost_basis,
                        "takeProfitStage": position.get("take_profit_stage", "entry"),
                        "openedAt": position.get("opened_at", ""),
                    }
                )
                continue
            slots.append({"slot": index + 1, "state": "empty"})
        return slots

    def _decision_snapshot(self, discovery: dict[str, Any]) -> dict[str, Any]:
        analyses = list(discovery.get("analyses") or [])
        recommended = discovery.get("recommended") or {}
        recommended_contract = str(recommended.get("candidate", {}).get("contract", ""))
        shortlisted: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        selected_snapshot: dict[str, Any] | None = None
        for item in analyses[:5]:
            candidate = item.get("candidate", {})
            snapshot = {
                "symbol": str(candidate.get("symbol", "")),
                "name": str(candidate.get("name", "")),
                "contract": str(candidate.get("contract", "")),
                "score": as_float(item.get("score")),
                "narrativeScore": as_float(item.get("narrative_score")),
                "communityScore": as_float(item.get("community_score")),
                "smartMoneyScore": as_float(item.get("smart_money_score")),
                "sources": list(item.get("sources") or []),
                "blockedReasons": list(item.get("blocked_reasons") or []),
                "warnings": list(item.get("warnings") or []),
                "whyChosen": self._why_chosen(item),
                "whyBlocked": self._why_blocked(item),
                "selected": str(candidate.get("contract", "")) == recommended_contract and bool(recommended_contract),
            }
            if snapshot["selected"]:
                selected_snapshot = snapshot
            if snapshot["blockedReasons"]:
                blocked.append(snapshot)
            else:
                shortlisted.append(snapshot)
        return {
            "rankingType": discovery.get("rankingType", self.state.get("ranking_type", "combined")),
            "riskMode": discovery.get("riskMode", self.state.get("risk_mode", "normal")),
            "selected": selected_snapshot,
            "shortlisted": shortlisted,
            "blocked": blocked,
        }

    def _why_chosen(self, item: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        sources = list(item.get("sources") or [])
        if len(sources) >= 2:
            reasons.append("Multi-ranking candidate")
        elif sources:
            reasons.append(f"Ranked by {sources[0]}")
        if as_float(item.get("liquidity_usd")) >= 100_000:
            reasons.append("Strong liquidity")
        if as_float(item.get("holders")) >= 500:
            reasons.append("Healthy holder base")
        if as_float(item.get("buy_pressure_ratio")) > 0.15:
            reasons.append("Positive buy pressure")
        if as_float(item.get("social_count")) >= 2:
            reasons.append("Visible social presence")
        if not reasons:
            reasons.append("Best remaining score")
        return reasons[:3]

    def _why_blocked(self, item: dict[str, Any]) -> list[str]:
        blocked = list(item.get("blocked_reasons") or [])
        if blocked:
            return blocked
        warnings = list(item.get("warnings") or [])
        return warnings[:3]

    def _discover_with_retry(
        self,
        *,
        ranking_type: str,
        risk_mode: str,
        excluded_contracts: set[str],
    ) -> dict[str, Any] | None:
        for attempt in range(2):
            try:
                return self.strategy.discover(
                    ranking_type,
                    risk_mode=risk_mode,
                    excluded_contracts=excluded_contracts,
                    limit=3,
                )
            except RateLimitedError:
                if attempt == 0:
                    logger.warning(
                        "auto discover rate limited; retrying once wait=%.2fs ranking_type=%s risk_mode=%s",
                        self.DISCOVER_RETRY_DELAY_SECONDS,
                        ranking_type,
                        risk_mode,
                    )
                    self.ledger.update_auto_run(last_action="discover_retry_wait")
                    self.stop_event.wait(self.DISCOVER_RETRY_DELAY_SECONDS)
                    continue
                logger.warning("auto discover skipped after retry due to repeated rate limit")
                self.ledger.record_risk_event("discover_rate_limited", "Discover skipped after retry")
                self.ledger.update_auto_run(last_action="discover_retry_exhausted")
                return None

    def _quote_with_retry(self, *, side: str, token_symbol: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return self.runner.order_quote(**kwargs)
            except RateLimitedError:
                if attempt == 0:
                    logger.warning(
                        "auto %s quote rate limited symbol=%s; retrying once wait=%.2fs",
                        side,
                        token_symbol,
                        self.QUOTE_RETRY_DELAY_SECONDS,
                    )
                    self.ledger.update_auto_run(last_action="quote_retry_wait")
                    self.stop_event.wait(self.QUOTE_RETRY_DELAY_SECONDS)
                    continue
                raise

    def _enter_cooldown(self, reason: str, seconds: float) -> None:
        self.cooldown_reason = reason
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + max(seconds, 1.0))
        self.ledger.update_auto_run(running=1, paused_reason=reason, last_action="cooldown_active")

    def _cooldown_active(self, *, update_ledger: bool = True) -> bool:
        if self.cooldown_until <= 0:
            return False
        if time.monotonic() < self.cooldown_until:
            return True
        self.cooldown_until = 0.0
        self.cooldown_reason = ""
        if update_ledger:
            self.ledger.update_auto_run(running=1, paused_reason="", last_action="cooldown_recovered")
        return False

    def _respect_quote_throttle(self, side: str, token_symbol: str) -> None:
        now = time.monotonic()
        elapsed = now - self.last_quote_attempt_at
        if elapsed >= self.QUOTE_MIN_INTERVAL_SECONDS:
            self.last_quote_attempt_at = now
            return
        wait = self.QUOTE_MIN_INTERVAL_SECONDS - elapsed
        logger.info("auto quote throttle side=%s symbol=%s wait=%.2fs", side, token_symbol, wait)
        self.ledger.update_auto_run(last_action="quote_throttle_wait")
        self.stop_event.wait(wait)
        self.last_quote_attempt_at = time.monotonic()

    def _poll_status(self, order_id: str) -> dict[str, Any]:
        payload = self.runner.order_status(order_id)
        for _ in range(self.poll_max):
            item = payload.get("data", payload)
            if item.get("status") in {"success", "failed", "refunded"}:
                return payload
            time.sleep(self.poll_interval)
            payload = self.runner.order_status(order_id)
        return payload

    def _require_order_data(self, order: dict[str, Any], operation: str) -> dict[str, Any]:
        if not isinstance(order, dict):
            raise ServiceError(f"{operation} returned a non-object response")
        order_data = order.get("data", order)
        if not isinstance(order_data, dict):
            raise ServiceError(f"{operation} returned empty order data")
        order_id = str(order_data.get("orderId", ""))
        if not order_id:
            raise ServiceError(f"{operation} did not return an orderId")
        return order_data
