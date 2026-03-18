from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from .auto_engine import AutoEngine
from .bgw_client import BgwError, CircuitOpenError, RateLimitedError
from .config import Settings, load_settings
from .ledger import Ledger, as_float, now_iso
from .models import AutoStartRequest, DiscoverRequest, OrderExecuteRequest, OrderPrepareRequest, SettingsPayload
from .runner import Runner, ServiceError, extract_tx_id
from .strategy import StrategyEngine, determine_quote_feature


def create_api_router(
    *,
    settings: Settings,
    ledger: Ledger,
    runner: Runner,
    strategy: StrategyEngine,
    auto_engine: AutoEngine,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    settings_attr_map = {
        "walletAddress": "wallet_address",
        "defaultBudgetSol": "default_budget_sol",
        "budgetSolMax": "budget_sol_max",
        "defaultMode": "default_mode",
        "rankingType": "default_ranking_type",
        "minLiquidityUsd": "min_liquidity_usd",
        "stopLossPct": "stop_loss_pct",
        "takeProfitCostBasisPct": "take_profit_cost_basis_pct",
        "takeProfitHalfPct": "take_profit_half_pct",
        "moonbagTriggerPct": "moonbag_trigger_pct",
        "moonbagFraction": "moonbag_fraction",
        "maxHoldHours": "max_hold_hours",
        "timeExitMaxGainPct": "time_exit_max_gain_pct",
        "discoverInterval": "discover_interval",
        "orderPollInterval": "order_poll_interval",
        "orderPollMax": "order_poll_max",
        "autoDailyLossLimitSol": "auto_daily_loss_limit_sol",
        "autoMaxConsecutiveLosses": "auto_max_consecutive_losses",
        "reserveSolBalance": "reserve_sol_balance",
    }

    def validate_budget(amount: float | None) -> None:
        if amount is None:
            return
        if amount > settings.budget_sol_max:
            raise HTTPException(status_code=400, detail=f"Budget exceeds configured max of {settings.budget_sol_max:.4f} SOL")

    def apply_runtime_settings(data: dict[str, Any]) -> None:
        for key, value in data.items():
            attr_name = settings_attr_map.get(key)
            if attr_name:
                setattr(settings, attr_name, value)
        if "walletAddress" in data:
            auto_engine.default_wallet = settings.wallet_address
        strategy.defaults.min_liquidity_usd = settings.min_liquidity_usd
        strategy.defaults.stop_loss_pct = settings.stop_loss_pct
        strategy.defaults.take_profit_cost_basis_pct = settings.take_profit_cost_basis_pct
        strategy.defaults.take_profit_half_pct = settings.take_profit_half_pct
        strategy.defaults.moonbag_trigger_pct = settings.moonbag_trigger_pct
        strategy.defaults.moonbag_fraction = settings.moonbag_fraction
        strategy.defaults.max_hold_hours = settings.max_hold_hours
        strategy.defaults.time_exit_max_gain_pct = settings.time_exit_max_gain_pct
        auto_engine.discover_interval = settings.discover_interval
        auto_engine.poll_interval = settings.order_poll_interval
        auto_engine.poll_max = settings.order_poll_max
        auto_engine.daily_loss_limit_sol = settings.auto_daily_loss_limit_sol
        auto_engine.max_consecutive_losses = settings.auto_max_consecutive_losses
        auto_engine.reserve_sol_balance = settings.reserve_sol_balance

    def refresh_runtime_credentials() -> None:
        fresh_settings = load_settings()
        stored_settings = ledger.get_settings_payload()
        wallet_address = str(
            stored_settings.get("walletAddress")
            or fresh_settings.wallet_address
            or settings.wallet_address
            or auto_engine.default_wallet
            or ""
        ).strip()
        private_key_sol = str(
            fresh_settings.private_key_sol
            or settings.private_key_sol
            or auto_engine.private_key_sol
            or ""
        ).strip()
        settings.wallet_address = wallet_address
        settings.private_key_sol = private_key_sol
        auto_engine.refresh_credentials(default_wallet=wallet_address, private_key_sol=private_key_sol)

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        if authorization == f"Bearer {settings.api_token}":
            return
        raise HTTPException(status_code=401, detail="Unauthorized")

    def handle_error(exc: Exception) -> None:
        if isinstance(exc, RateLimitedError):
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if isinstance(exc, CircuitOpenError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, (BgwError, ServiceError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise exc

    @router.get("/summary")
    def summary(_: None = Depends(require_auth)) -> dict[str, Any]:
        data = ledger.summarize()
        data["autoStatus"] = auto_engine.status()
        return {"ok": True, "data": data}

    @router.post("/discover")
    def discover(payload: DiscoverRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        try:
            result = strategy.discover(payload.rankingType, risk_mode=payload.riskMode, limit=3)
        except Exception as exc:
            handle_error(exc)
        recommended = result["recommended"] or {}
        return {
            "ok": True,
            "data": {
                "rankingType": payload.rankingType,
                "riskMode": payload.riskMode,
                "candidates": result["analyses"],
                "recommended": recommended,
            },
        }

    @router.post("/order/prepare")
    def prepare_order(payload: OrderPrepareRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        try:
            if payload.side == "buy":
                amount = payload.budgetSol or settings.default_budget_sol
                validate_budget(amount)
                from_contract, to_contract = "", payload.tokenContract
            else:
                amount = payload.tokenAmount or 0.0
                from_contract, to_contract = payload.tokenContract, ""
            quote = runner.order_quote(
                from_chain="sol",
                from_contract=from_contract,
                to_chain="sol",
                to_contract=to_contract,
                amount=f"{amount:.8f}",
                from_address=payload.walletAddress or settings.wallet_address,
            )
        except Exception as exc:
            handle_error(exc)
        quote_summary = strategy.summarize_quote(quote)
        response: dict[str, Any] = {
            "mode": payload.mode,
            "side": payload.side,
            "tokenContract": payload.tokenContract,
            "amount": amount,
            "quote": quote_summary,
            "handoff": None,
        }
        if payload.mode in {"semi_auto_live", "auto_live"} and quote_summary["market"]:
            feature = determine_quote_feature(quote, amount)
            response["handoff"] = {
                "market": quote_summary["market"],
                "feature": feature,
                "walletAddress": payload.walletAddress or settings.wallet_address,
            }
        return {"ok": True, "data": response}

    @router.post("/order/execute")
    def execute_order(payload: OrderExecuteRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        try:
            if payload.side == "buy":
                validate_budget(payload.budgetSol)
            if payload.mode == "paper":
                return {"ok": True, "data": _execute_paper_trade(payload, ledger, runner, strategy)}
            if payload.mode == "semi_auto_live":
                return {"ok": True, "data": _prepare_live_trade(payload, ledger, runner, settings, strategy)}
            return {"ok": True, "data": _execute_auto_trade(payload, ledger, runner, settings, strategy)}
        except Exception as exc:
            handle_error(exc)
        return {"ok": False, "data": {}}

    @router.post("/positions/{position_ref}/sell")
    def sell_position(position_ref: str, mode: str = "paper", _: None = Depends(require_auth)) -> dict[str, Any]:
        position = _resolve_position_reference(ledger, position_ref)
        if not position:
            raise HTTPException(status_code=404, detail="Position not found")
        amount = _open_position_amount(position)
        payload = OrderExecuteRequest(
            side="sell",
            tokenContract=str(position["token_contract"]),
            positionId=int(position["id"]),
            tokenAmount=amount,
            mode=mode,  # type: ignore[arg-type]
            walletAddress=settings.wallet_address,
        )
        return execute_order(payload)

    @router.get("/orders/{order_id}")
    def order_status(order_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        db_order = ledger.get_order(order_id=order_id)
        remote_status = None
        reconciled = False
        if db_order and db_order.get("status") in {"prepared", "submitted", "polling"}:
            try:
                remote_status = runner.order_status(order_id)
                reconciled = _reconcile_remote_order(
                    db_order=db_order,
                    remote_status=remote_status,
                    ledger=ledger,
                    strategy=strategy,
                    settings=settings,
                )
            except Exception as exc:
                handle_error(exc)
            db_order = ledger.get_order(order_id=order_id)
        return {"ok": True, "data": {"db": db_order or {}, "remote": remote_status or {}, "reconciled": reconciled}}

    @router.get("/trades")
    def trades(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": ledger.list_trades()}

    @router.get("/positions")
    def positions(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": ledger.list_positions()}

    @router.get("/pnl")
    def pnl(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": {"rows": ledger.pnl_by_token(), "summary": ledger.pnl_overview()}}

    @router.get("/settings")
    def get_settings(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": ledger.get_settings_payload()}

    @router.post("/settings")
    def set_settings(payload: SettingsPayload, _: None = Depends(require_auth)) -> dict[str, Any]:
        data = payload.model_dump(exclude_none=True)
        if "budgetSolMax" in data and "defaultBudgetSol" in data and data["defaultBudgetSol"] > data["budgetSolMax"]:
            raise HTTPException(status_code=400, detail="Default budget cannot exceed budget max")
        if "budgetSolMax" in data and data["budgetSolMax"] < settings.default_budget_sol and "defaultBudgetSol" not in data:
            raise HTTPException(status_code=400, detail="Budget max cannot be lower than the current default budget")
        if "defaultBudgetSol" in data:
            max_budget = data.get("budgetSolMax", settings.budget_sol_max)
            if data["defaultBudgetSol"] > max_budget:
                raise HTTPException(status_code=400, detail="Default budget cannot exceed budget max")
        for key, value in data.items():
            ledger.set_setting(key, value)
        apply_runtime_settings(data)
        return {"ok": True, "data": ledger.get_settings_payload()}

    @router.post("/paper/clear")
    def clear_paper(_: None = Depends(require_auth)) -> dict[str, Any]:
        cleared = ledger.clear_paper_data()
        data = ledger.summarize()
        data["autoStatus"] = auto_engine.status()
        return {"ok": True, "data": {"cleared": cleared, "summary": data}}

    @router.post("/history/clear")
    def clear_history(_: None = Depends(require_auth)) -> dict[str, Any]:
        auto_engine.stop()
        cleared = ledger.clear_all_history_data()
        data = ledger.summarize()
        data["autoStatus"] = auto_engine.status()
        return {"ok": True, "data": {"cleared": cleared, "summary": data}}

    @router.post("/auto/start")
    def auto_start(payload: AutoStartRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        validate_budget(payload.budgetSol)
        refresh_runtime_credentials()
        status = auto_engine.start(ranking_type=payload.rankingType, budget_sol=payload.budgetSol, risk_mode=payload.riskMode)
        return {"ok": True, "data": status}

    @router.post("/auto/stop")
    def auto_stop(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": auto_engine.stop()}

    @router.get("/auto/status")
    def auto_status(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"ok": True, "data": auto_engine.status()}

    return router


def _execute_paper_trade(payload: OrderExecuteRequest, ledger: Ledger, runner: Runner, strategy: StrategyEngine) -> dict[str, Any]:
    client_trade_id = payload.clientTradeId or f"paper-{uuid.uuid4().hex[:12]}"
    position = _resolve_position_for_sell(ledger, payload) if payload.side == "sell" else None
    amount = payload.budgetSol or payload.tokenAmount or 0.0
    if payload.side == "buy":
        quote = runner.order_quote(
            from_chain="sol",
            from_contract="",
            to_chain="sol",
            to_contract=payload.tokenContract,
            amount=f"{amount:.8f}",
            from_address=payload.walletAddress or "",
        )
        summary = strategy.summarize_quote(quote)
        current_price_sol, _ = strategy.current_price_in_sol(payload.tokenContract)
        received = _resolve_buy_amount(
            quoted_amount=summary["toAmount"],
            spend_amount=amount,
            token_contract=payload.tokenContract,
            strategy=strategy,
            current_price_sol=current_price_sol,
        )
        if received <= 0:
            raise ServiceError("Could not determine paper buy fill amount")
        entry_price_sol = amount / received if received else 0.0
        ledger.create_order(
            {
                "order_id": f"paper-{uuid.uuid4().hex[:8]}",
                "client_trade_id": client_trade_id,
                "side": "buy",
                "token_symbol": response_token_symbol(strategy, payload.tokenContract),
                "token_contract": payload.tokenContract,
                "mode": "paper",
                "status": "success",
                "market": summary["market"],
                "amount_in_sol": amount,
                "amount_out": received,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        ledger.add_trade(
            {
                "client_trade_id": client_trade_id,
                "order_id": f"paper-{uuid.uuid4().hex[:8]}",
                "side": "buy",
                "token_symbol": response_token_symbol(strategy, payload.tokenContract),
                "token_contract": payload.tokenContract,
                "amount_in_sol": amount,
                "amount_out": received,
                "mode": "paper",
                "status": "success",
                "note": "paper buy",
            }
        )
        ledger.upsert_position(
            {
                "token_contract": payload.tokenContract,
                "token_symbol": response_token_symbol(strategy, payload.tokenContract),
                "entry_price_sol": entry_price_sol,
                "current_price_sol": current_price_sol,
                "amount": received,
                "market_value_sol": received * current_price_sol,
                "cost_basis_sol": amount,
                "realized_pnl_sol": 0.0,
                "peak_price_sol": current_price_sol,
                "take_profit_stage": "entry",
                "mode": "paper",
                "opened_at": now_iso(),
                "status": "open",
                "updated_at": now_iso(),
            }
        )
        ledger.add_pnl_snapshot(
            "paper",
            realized=0.0,
            unrealized=(received * current_price_sol) - amount,
            open_positions=1,
            token_symbol=response_token_symbol(strategy, payload.tokenContract),
            token_contract=payload.tokenContract,
        )
        return {"clientTradeId": client_trade_id, "status": "success", "mode": "paper"}
    if not position:
        raise ServiceError("Open position not found for paper sell")
    amount_tokens = payload.tokenAmount or _open_position_amount(position)
    if amount_tokens <= 0:
        raise ServiceError("Open position amount is zero. Refresh positions before selling.")
    current_price_sol, _ = strategy.current_price_in_sol(payload.tokenContract)
    if current_price_sol <= 0:
        raise ServiceError("Could not determine current token price for paper sell")
    receive_amount = amount_tokens * current_price_sol
    realized = receive_amount - (amount_tokens * as_float(position["entry_price_sol"]))
    ledger.add_trade(
        {
            "client_trade_id": client_trade_id,
            "side": "sell",
            "position_id": int(position["id"]),
            "token_symbol": position["token_symbol"],
            "token_contract": payload.tokenContract,
            "amount_out": receive_amount,
            "mode": "paper",
            "status": "success",
            "note": "paper sell",
        }
    )
    ledger.upsert_position(
        {
            **position,
            "amount": 0.0,
            "market_value_sol": 0.0,
            "realized_pnl_sol": realized,
            "closed_at": now_iso(),
            "status": "closed",
            "updated_at": now_iso(),
        }
    )
    ledger.add_pnl_snapshot(
        "paper",
        realized=realized,
        unrealized=0.0,
        open_positions=0,
        token_symbol=position["token_symbol"],
        token_contract=payload.tokenContract,
    )
    return {"clientTradeId": client_trade_id, "status": "success", "mode": "paper"}


def _prepare_live_trade(
    payload: OrderExecuteRequest,
    ledger: Ledger,
    runner: Runner,
    settings: Settings,
    strategy: StrategyEngine,
) -> dict[str, Any]:
    active_order = ledger.get_active_order()
    if active_order:
        resumed = _resume_active_live_order(
            active_order=active_order,
            payload=payload,
            ledger=ledger,
            runner=runner,
            settings=settings,
            strategy=strategy,
            allow_submit=False,
        )
        if resumed is not None:
            return resumed
        blocking_order = _refresh_or_clear_active_order(
            active_order=active_order,
            ledger=ledger,
            runner=runner,
            strategy=strategy,
            settings=settings,
        )
        if blocking_order is not None:
            active_token = str(blocking_order.get("token_symbol") or blocking_order.get("token_contract") or "another token")
            raise ServiceError(f"An active order is still running for {active_token}. Refresh order status before creating a new order.")
    amount = payload.budgetSol or payload.tokenAmount or 0.0
    from_contract, to_contract = ("", payload.tokenContract) if payload.side == "buy" else (payload.tokenContract, "")
    position_id = _position_id_for_payload(ledger, payload)
    quote = runner.order_quote(
        from_chain="sol",
        from_contract=from_contract,
        to_chain="sol",
        to_contract=to_contract,
        amount=f"{amount:.8f}",
        from_address=payload.walletAddress or settings.wallet_address,
    )
    summary = strategy.summarize_quote(quote)
    feature = determine_quote_feature(quote, amount)
    order = runner.order_create(
        from_chain="sol",
        from_contract=from_contract,
        to_chain="sol",
        to_contract=to_contract,
        amount=f"{amount:.8f}",
        from_address=payload.walletAddress or settings.wallet_address,
        market=summary["market"],
        feature=feature,
    )
    order_data = _require_order_data(order, "order_create")
    order_id = str(order_data.get("orderId", ""))
    client_trade_id = payload.clientTradeId or f"semi-{uuid.uuid4().hex[:12]}"
    ledger.create_order(
        {
            "order_id": order_id,
            "client_trade_id": client_trade_id,
            "side": payload.side,
            "position_id": position_id,
            "token_symbol": response_token_symbol(strategy, payload.tokenContract),
            "token_contract": payload.tokenContract,
            "mode": "semi_auto_live",
            "status": "prepared",
            "market": summary["market"],
            "amount_in_sol": amount if payload.side == "buy" else 0.0,
            "amount_out": summary["toAmount"],
            "raw_order_json": order,
        }
    )
    if payload.signedTxs:
        submit = runner.order_submit(order_id, payload.signedTxs)
        final = _poll_order_status(runner, order_id)
        final_item = final.get("data", final)
        tx_id = extract_tx_id(final)
        ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id)
        _apply_live_position_update(
            payload=payload,
            ledger=ledger,
            strategy=strategy,
            token_symbol=response_token_symbol(strategy, payload.tokenContract),
            final_item=final_item,
            tx_id=tx_id,
            client_trade_id=client_trade_id,
            order_id=order_id,
        )
        return {"clientTradeId": client_trade_id, "orderId": order_id, "status": final_item.get("status", "unknown"), "txId": tx_id}
    return {
        "clientTradeId": client_trade_id,
        "orderId": order_id,
        "status": "prepared",
        "handoff": {
            "message": "Use the external wallet to sign and submit. Then refresh order status.",
            "market": summary["market"],
            "feature": feature,
            "walletAddress": payload.walletAddress or settings.wallet_address,
        },
    }


def _execute_auto_trade(
    payload: OrderExecuteRequest,
    ledger: Ledger,
    runner: Runner,
    settings: Settings,
    strategy: StrategyEngine,
) -> dict[str, Any]:
    if not settings.private_key_sol:
        raise ServiceError("HMS_SOL_PRIVATE_KEY is required for auto_live")
    active_order = ledger.get_active_order()
    if active_order:
        resumed = _resume_active_live_order(
            active_order=active_order,
            payload=payload,
            ledger=ledger,
            runner=runner,
            settings=settings,
            strategy=strategy,
            allow_submit=True,
        )
        if resumed is not None:
            return resumed
        blocking_order = _refresh_or_clear_active_order(
            active_order=active_order,
            ledger=ledger,
            runner=runner,
            strategy=strategy,
            settings=settings,
        )
        if blocking_order is not None:
            active_token = str(blocking_order.get("token_symbol") or blocking_order.get("token_contract") or "another token")
            raise ServiceError(f"An active order is still running for {active_token}. Wait for it to finish.")
    amount = payload.budgetSol or payload.tokenAmount or 0.0
    from_contract, to_contract = ("", payload.tokenContract) if payload.side == "buy" else (payload.tokenContract, "")
    position_id = _position_id_for_payload(ledger, payload)
    quote = runner.order_quote(
        from_chain="sol",
        from_contract=from_contract,
        to_chain="sol",
        to_contract=to_contract,
        amount=f"{amount:.8f}",
        from_address=payload.walletAddress or settings.wallet_address,
    )
    summary = strategy.summarize_quote(quote)
    feature = determine_quote_feature(quote, amount)
    order = runner.order_create(
        from_chain="sol",
        from_contract=from_contract,
        to_chain="sol",
        to_contract=to_contract,
        amount=f"{amount:.8f}",
        from_address=payload.walletAddress or settings.wallet_address,
        market=summary["market"],
        feature=feature,
    )
    order_data = _require_order_data(order, "order_create")
    order_id = str(order_data.get("orderId", ""))
    client_trade_id = payload.clientTradeId or f"auto-{uuid.uuid4().hex[:12]}"
    ledger.create_order(
        {
            "order_id": order_id,
            "client_trade_id": client_trade_id,
            "side": payload.side,
            "position_id": position_id,
            "token_symbol": response_token_symbol(strategy, payload.tokenContract),
            "token_contract": payload.tokenContract,
            "mode": "auto_live",
            "status": "prepared",
            "market": summary["market"],
            "amount_in_sol": amount if payload.side == "buy" else 0.0,
            "amount_out": summary["toAmount"],
            "raw_order_json": order,
        }
    )
    signed = runner.sign_order(order, private_key_sol=settings.private_key_sol)
    runner.order_submit(order_id, signed)
    final = _poll_order_status(runner, order_id)
    final_item = final.get("data", final)
    tx_id = extract_tx_id(final)
    ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id)
    _apply_live_position_update(
        payload=payload,
        ledger=ledger,
        strategy=strategy,
        token_symbol=response_token_symbol(strategy, payload.tokenContract),
        final_item=final_item,
        tx_id=tx_id,
        client_trade_id=client_trade_id,
        order_id=order_id,
    )
    return {"clientTradeId": client_trade_id, "orderId": order_id, "status": final_item.get("status", "unknown"), "txId": tx_id}


def response_token_symbol(strategy: StrategyEngine, token_contract: str) -> str:
    token_info = strategy.runner.token_info("sol", token_contract)
    item = token_info.get("data", token_info)
    return str(item.get("symbol", "TOKEN"))


def _open_position_amount(position: dict[str, Any]) -> float:
    if position.get("status") != "open":
        raise ServiceError("Position is not open")
    amount = as_float(position.get("amount"))
    if amount <= 0:
        raise ServiceError("Open position amount is zero. Refresh positions before selling.")
    return amount


def _resolve_buy_amount(
    *,
    quoted_amount: float,
    spend_amount: float,
    token_contract: str,
    strategy: StrategyEngine,
    current_price_sol: float | None = None,
) -> float:
    if quoted_amount > 0:
        return quoted_amount
    spot_price = current_price_sol if current_price_sol is not None else strategy.current_price_in_sol(token_contract)[0]
    if spend_amount > 0 and spot_price > 0:
        return spend_amount / spot_price
    return 0.0


def _poll_order_status(runner: Runner, order_id: str, poll_interval: float = 1.0, poll_max: int = 4) -> dict[str, Any]:
    payload = runner.order_status(order_id)
    for _ in range(poll_max - 1):
        item = payload.get("data", payload)
        if item.get("status") in {"success", "failed", "refunded"}:
            return payload
        time.sleep(poll_interval)
        payload = runner.order_status(order_id)
    return payload


def _require_order_data(order: dict[str, Any], operation: str) -> dict[str, Any]:
    if not isinstance(order, dict):
        raise ServiceError(f"{operation} returned a non-object response")
    order_data = order.get("data", order)
    if not isinstance(order_data, dict):
        raise ServiceError(f"{operation} returned empty order data")
    order_id = str(order_data.get("orderId", ""))
    if not order_id:
        raise ServiceError(f"{operation} did not return an orderId")
    return order_data


def _is_missing_remote_order_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("http 404", "404:", "not found", "does not exist", "unknown order"))


def _refresh_or_clear_active_order(
    *,
    active_order: dict[str, Any],
    ledger: Ledger,
    runner: Runner,
    strategy: StrategyEngine,
    settings: Settings,
) -> dict[str, Any] | None:
    client_trade_id = str(active_order.get("client_trade_id") or "")
    active_order_id = str(active_order.get("order_id") or "")
    if not client_trade_id:
        return None
    if not active_order_id:
        ledger.update_order(client_trade_id, status="failed", error_reason="invalid_active_order_missing_order_id")
        return None
    try:
        remote_status = runner.order_status(active_order_id)
    except (RateLimitedError, CircuitOpenError):
        raise
    except Exception as exc:
        if _is_missing_remote_order_error(exc):
            ledger.update_order(client_trade_id, status="failed", error_reason="remote_order_not_found")
            return None
        return active_order
    remote_item = remote_status.get("data", remote_status)
    remote_state = str(remote_item.get("status", "")).lower()
    if remote_state in {"success", "failed", "refunded"}:
        _reconcile_remote_order(
            db_order=active_order,
            remote_status=remote_status,
            ledger=ledger,
            strategy=strategy,
            settings=settings,
        )
        return None
    return ledger.get_order(client_trade_id=client_trade_id) or active_order


def _resume_active_live_order(
    *,
    active_order: dict[str, Any],
    payload: OrderExecuteRequest,
    ledger: Ledger,
    runner: Runner,
    settings: Settings,
    strategy: StrategyEngine,
    allow_submit: bool,
) -> dict[str, Any] | None:
    active_side = str(active_order.get("side") or "")
    active_contract = str(active_order.get("token_contract") or "")
    active_mode = str(active_order.get("mode") or "")
    active_status = str(active_order.get("status") or "")
    active_order_id = str(active_order.get("order_id") or "")
    client_trade_id = str(active_order.get("client_trade_id") or "")
    active_position_id = int(active_order.get("position_id") or 0)
    payload_position_id = int(payload.positionId or 0)
    if (
        not active_order_id
        or active_side != payload.side
        or active_contract != payload.tokenContract
        or active_mode != payload.mode
        or (payload_position_id and active_position_id and payload_position_id != active_position_id)
    ):
        return None

    if active_status == "submitted":
        final = _poll_order_status(runner, active_order_id)
        final_item = final.get("data", final)
        tx_id = extract_tx_id(final)
        ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id)
        _apply_live_position_update(
            payload=payload,
            ledger=ledger,
            strategy=strategy,
            token_symbol=str(active_order.get("token_symbol") or response_token_symbol(strategy, payload.tokenContract)),
            final_item=final_item,
            tx_id=tx_id,
            client_trade_id=client_trade_id,
            order_id=active_order_id,
        )
        return {
            "clientTradeId": client_trade_id,
            "orderId": active_order_id,
            "status": final_item.get("status", "unknown"),
            "txId": tx_id,
            "resumed": True,
        }

    if active_status != "prepared" or not allow_submit:
        return None

    raw_order = active_order.get("raw_order_json")
    if not raw_order:
        return None
    try:
        order_response = json.loads(raw_order) if isinstance(raw_order, str) else raw_order
    except json.JSONDecodeError:
        return None
    signed = runner.sign_order(order_response, private_key_sol=settings.private_key_sol)
    try:
        runner.order_submit(active_order_id, signed)
    except RateLimitedError:
        ledger.update_order(client_trade_id, status="prepared", error_reason="order_submit_rate_limited")
        raise
    final = _poll_order_status(runner, active_order_id)
    final_item = final.get("data", final)
    tx_id = extract_tx_id(final)
    ledger.update_order(client_trade_id, status=final_item.get("status", "unknown"), tx_id=tx_id)
    _apply_live_position_update(
        payload=payload,
        ledger=ledger,
        strategy=strategy,
        token_symbol=str(active_order.get("token_symbol") or response_token_symbol(strategy, payload.tokenContract)),
        final_item=final_item,
        tx_id=tx_id,
        client_trade_id=client_trade_id,
        order_id=active_order_id,
    )
    return {
        "clientTradeId": client_trade_id,
        "orderId": active_order_id,
        "status": final_item.get("status", "unknown"),
        "txId": tx_id,
        "resumed": True,
    }


def _reconcile_remote_order(
    *,
    db_order: dict[str, Any],
    remote_status: dict[str, Any],
    ledger: Ledger,
    strategy: StrategyEngine,
    settings: Settings,
) -> bool:
    remote_item = remote_status.get("data", remote_status)
    status = str(remote_item.get("status", "unknown"))
    tx_id = extract_tx_id(remote_status)
    ledger.update_order(str(db_order["client_trade_id"]), status=status, tx_id=tx_id)
    if status not in {"success", "failed", "refunded"}:
        return False
    client_trade_id = str(db_order["client_trade_id"])
    if ledger.has_trade(client_trade_id):
        return False
    payload = OrderExecuteRequest(
        side=str(db_order["side"]),
        tokenContract=str(db_order["token_contract"]),
        positionId=int(db_order.get("position_id") or 0) or None,
        budgetSol=as_float(db_order.get("amount_in_sol")) or None,
        mode=str(db_order["mode"]),  # type: ignore[arg-type]
        walletAddress=settings.wallet_address or None,
        clientTradeId=client_trade_id,
    )
    _apply_live_position_update(
        payload=payload,
        ledger=ledger,
        strategy=strategy,
        token_symbol=str(db_order.get("token_symbol") or response_token_symbol(strategy, payload.tokenContract)),
        final_item=remote_item,
        tx_id=tx_id,
        client_trade_id=client_trade_id,
        order_id=str(db_order.get("order_id", "")),
    )
    return True


def _apply_live_position_update(
    *,
    payload: OrderExecuteRequest,
    ledger: Ledger,
    strategy: StrategyEngine,
    token_symbol: str,
    final_item: dict[str, Any],
    tx_id: str,
    client_trade_id: str,
    order_id: str,
) -> None:
    status = str(final_item.get("status", "unknown"))
    position = _resolve_position_for_sell(ledger, payload) if payload.side == "sell" else None
    received_amount = as_float(final_item.get("receiveAmount") or final_item.get("toAmount"))
    if status == "success" and payload.side == "buy":
        budget = payload.budgetSol or 0.0
        current_price_sol, _ = strategy.current_price_in_sol(payload.tokenContract)
        received_amount = _resolve_buy_amount(
            quoted_amount=received_amount,
            spend_amount=budget,
            token_contract=payload.tokenContract,
            strategy=strategy,
            current_price_sol=current_price_sol,
        )
        if received_amount <= 0:
            raise ServiceError("Could not determine buy fill amount from live order")
    elif status == "success" and payload.side == "sell":
        token_amount = payload.tokenAmount or _open_position_amount(position)
        if received_amount <= 0:
            current_price_sol = as_float(position.get("current_price_sol"))
            if current_price_sol <= 0:
                current_price_sol, _ = strategy.current_price_in_sol(payload.tokenContract)
            received_amount = token_amount * current_price_sol
    ledger.add_trade(
        {
            "client_trade_id": client_trade_id,
            "order_id": order_id,
            "position_id": int(position["id"]) if payload.side == "sell" and position else payload.positionId,
            "side": payload.side,
            "token_symbol": token_symbol,
            "token_contract": payload.tokenContract,
            "amount_in_sol": payload.budgetSol or 0.0,
            "amount_out": received_amount,
            "mode": payload.mode,
            "status": status,
            "tx_id": tx_id,
            "note": f"{payload.mode} {payload.side}",
        }
    )
    if status != "success":
        return
    if payload.side == "buy":
        entry_price_sol = budget / received_amount if received_amount else 0.0
        ledger.upsert_position(
            {
                "token_contract": payload.tokenContract,
                "token_symbol": token_symbol,
                "entry_price_sol": entry_price_sol,
                "current_price_sol": current_price_sol,
                "amount": received_amount,
                "market_value_sol": received_amount * current_price_sol,
                "cost_basis_sol": budget,
                "realized_pnl_sol": 0.0,
                "peak_price_sol": current_price_sol,
                "take_profit_stage": "entry",
                "mode": payload.mode,
                "opened_at": now_iso(),
                "status": "open",
                "updated_at": now_iso(),
            }
        )
        ledger.add_pnl_snapshot(
            payload.mode,
            realized=0.0,
            unrealized=(received_amount * current_price_sol) - budget,
            open_positions=len(ledger.open_positions()),
            token_symbol=token_symbol,
            token_contract=payload.tokenContract,
        )
        return
    position = _resolve_position_for_sell(ledger, payload)
    token_amount = payload.tokenAmount or _open_position_amount(position)
    entry_price_sol = as_float(position.get("entry_price_sol"))
    realized = received_amount - (token_amount * entry_price_sol)
    ledger.upsert_position(
        {
            **position,
            "amount": 0.0,
            "market_value_sol": 0.0,
            "realized_pnl_sol": as_float(position.get("realized_pnl_sol")) + realized,
            "closed_at": now_iso(),
            "status": "closed",
            "updated_at": now_iso(),
        }
    )
    ledger.add_pnl_snapshot(
        payload.mode,
        realized=realized,
        unrealized=0.0,
        open_positions=len(ledger.open_positions()),
        token_symbol=position["token_symbol"],
        token_contract=payload.tokenContract,
    )


def _resolve_position_reference(ledger: Ledger, position_ref: str) -> dict[str, Any] | None:
    if position_ref.isdigit():
        position = ledger.get_position(int(position_ref))
        if position:
            return position
    return ledger.latest_open_position(position_ref)


def _resolve_position_for_sell(ledger: Ledger, payload: OrderExecuteRequest) -> dict[str, Any]:
    if payload.positionId:
        position = ledger.get_position(payload.positionId)
        if not position:
            raise ServiceError("Position not found for sell")
        if str(position.get("token_contract") or "") != payload.tokenContract:
            raise ServiceError("Position does not match the requested token contract")
        if position.get("status") != "open":
            raise ServiceError("Position is not open")
        return position
    position = ledger.latest_open_position(payload.tokenContract)
    if not position:
        raise ServiceError("Open position not found")
    return position


def _position_id_for_payload(ledger: Ledger, payload: OrderExecuteRequest) -> int | None:
    if payload.side != "sell":
        return None
    if payload.positionId:
        return int(_resolve_position_for_sell(ledger, payload)["id"])
    position = ledger.latest_open_position(payload.tokenContract)
    return int(position["id"]) if position else None
