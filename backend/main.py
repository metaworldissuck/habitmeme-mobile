from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api_routes import create_api_router
from .auto_engine import AutoEngine
from .bgw_client import BgwClient
from .config import Settings, load_settings
from .ledger import Ledger
from .runner import Runner
from .strategy import StrategyDefaults, StrategyEngine
from .web_routes import create_web_router


def build_runtime() -> dict[str, Any]:
    settings = load_settings()
    ledger = Ledger(settings)
    ledger.initialize()
    client = BgwClient(
        api_key=os.getenv("BGW_API_KEY"),
        api_secret=os.getenv("BGW_API_SECRET"),
        partner_code=os.getenv("BGW_PARTNER_CODE", "bgw_swap_public"),
        event_sink=ledger.record_api_event,
    )
    runner = Runner(client)
    strategy = StrategyEngine(
        runner,
        StrategyDefaults(
            min_liquidity_usd=settings.min_liquidity_usd,
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_cost_basis_pct=settings.take_profit_cost_basis_pct,
            take_profit_half_pct=settings.take_profit_half_pct,
            moonbag_trigger_pct=settings.moonbag_trigger_pct,
            moonbag_fraction=settings.moonbag_fraction,
            max_hold_hours=settings.max_hold_hours,
            time_exit_max_gain_pct=settings.time_exit_max_gain_pct,
        ),
    )
    auto_engine = AutoEngine(
        ledger=ledger,
        runner=runner,
        strategy=strategy,
        default_wallet=settings.wallet_address,
        private_key_sol=settings.private_key_sol,
        discover_interval=settings.discover_interval,
        poll_interval=settings.order_poll_interval,
        poll_max=settings.order_poll_max,
        reserve_sol_balance=settings.reserve_sol_balance,
        position_sizing_mode=settings.position_sizing_mode,
        daily_loss_limit_sol=settings.auto_daily_loss_limit_sol,
        max_consecutive_losses=settings.auto_max_consecutive_losses,
    )
    templates = Jinja2Templates(directory=str(settings.db_path.parents[0] / "templates"))
    templates.env.auto_reload = True
    return {
        "settings": settings,
        "ledger": ledger,
        "runner": runner,
        "strategy": strategy,
        "auto_engine": auto_engine,
        "templates": templates,
    }


def create_app() -> FastAPI:
    runtime = build_runtime()
    settings: Settings = runtime["settings"]
    templates: Jinja2Templates = runtime["templates"]

    app = FastAPI(title="HabitMeme Mobile", version="0.1.0")
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=str(settings.db_path.parents[0] / "static")), name="static")
    app.include_router(
        create_api_router(
            settings=runtime["settings"],
            ledger=runtime["ledger"],
            runner=runtime["runner"],
            strategy=runtime["strategy"],
            auto_engine=runtime["auto_engine"],
        )
    )
    app.include_router(create_web_router(templates))

    @app.middleware("http")
    async def disable_cache(request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        if request.url.path.startswith(("/app", "/api", "/static")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "habitmeme-mobile",
            "walletConfigured": bool(settings.wallet_address),
            "privateKeyConfigured": bool(settings.private_key_sol),
            "apiTokenConfigured": bool(settings.api_token),
            "dynamicReload": True,
        }

    @app.exception_handler(Exception)
    def unhandled_exception(_, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    return app


app = create_app()
