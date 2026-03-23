from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = ROOT.parents[1] / "bitget-wallet-skill"
DEFAULT_DB_PATH = ROOT / "backend" / "habitmeme.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_ENV_PATH = ROOT / ".env"


def _load_project_env(env_path: Path = DEFAULT_ENV_PATH) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


_load_project_env()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    host: str = os.getenv("HMS_HOST", DEFAULT_HOST)
    port: int = int(os.getenv("HMS_PORT", str(DEFAULT_PORT)))
    db_path: Path = Path(os.getenv("HMS_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    legacy_root: Path = Path(os.getenv("HMS_LEGACY_ROOT", str(LEGACY_ROOT))).expanduser()
    wallet_address: str = os.getenv("HMS_SOL_ADDRESS", "")
    private_key_sol: str = os.getenv("HMS_SOL_PRIVATE_KEY", "")
    default_budget_sol: float = float(os.getenv("HMS_DEFAULT_BUDGET_SOL", "0.02"))
    budget_sol_max: float = float(os.getenv("HMS_BUDGET_SOL_MAX", "0.1"))
    default_mode: str = os.getenv("HMS_MODE_DEFAULT", "paper")
    default_ranking_type: str = os.getenv("HMS_RANKING_TYPE_DEFAULT", "combined")
    auto_enabled: bool = _env_bool("HMS_AUTO_ENABLED", False)
    api_token: str = os.getenv("HMS_API_TOKEN", "local-dev-token")
    min_liquidity_usd: float = float(os.getenv("HMS_MIN_LIQUIDITY_USD", "60000"))
    stop_loss_pct: float = float(os.getenv("HMS_STOP_LOSS_PCT", "0.12"))
    take_profit_cost_basis_pct: float = float(os.getenv("HMS_TAKE_PROFIT_COST_BASIS_PCT", "0.45"))
    take_profit_half_pct: float = float(os.getenv("HMS_TAKE_PROFIT_HALF_PCT", "0.9"))
    moonbag_trigger_pct: float = float(os.getenv("HMS_MOONBAG_TRIGGER_PCT", "1.8"))
    moonbag_fraction: float = float(os.getenv("HMS_MOONBAG_FRACTION", "0.1"))
    max_hold_hours: float = float(os.getenv("HMS_MAX_HOLD_HOURS", "18"))
    time_exit_max_gain_pct: float = float(os.getenv("HMS_TIME_EXIT_MAX_GAIN_PCT", "0.1"))
    discover_interval: int = int(os.getenv("HMS_DISCOVER_INTERVAL", "90"))
    order_poll_interval: int = int(os.getenv("HMS_ORDER_POLL_INTERVAL", "8"))
    order_poll_max: int = int(os.getenv("HMS_ORDER_POLL_MAX", "6"))
    auto_max_consecutive_losses: int = int(os.getenv("HMS_AUTO_MAX_CONSECUTIVE_LOSSES", "2"))
    auto_daily_loss_limit_sol: float = float(os.getenv("HMS_AUTO_DAILY_LOSS_LIMIT_SOL", "0.03"))
    reserve_sol_balance: float = float(os.getenv("HMS_RESERVE_SOL_BALANCE", "0.02"))
    position_sizing_mode: str = os.getenv("HMS_POSITION_SIZING_MODE", "slot_cap")

    def as_public_dict(self) -> dict[str, object]:
        return {
            "walletAddress": self.wallet_address,
            "defaultBudgetSol": self.default_budget_sol,
            "budgetSolMax": self.budget_sol_max,
            "defaultMode": self.default_mode,
            "riskMode": "normal",
            "rankingType": self.default_ranking_type,
            "minLiquidityUsd": self.min_liquidity_usd,
            "stopLossPct": self.stop_loss_pct,
            "takeProfitCostBasisPct": self.take_profit_cost_basis_pct,
            "takeProfitHalfPct": self.take_profit_half_pct,
            "moonbagTriggerPct": self.moonbag_trigger_pct,
            "moonbagFraction": self.moonbag_fraction,
            "maxHoldHours": self.max_hold_hours,
            "timeExitMaxGainPct": self.time_exit_max_gain_pct,
            "discoverInterval": self.discover_interval,
            "orderPollInterval": self.order_poll_interval,
            "orderPollMax": self.order_poll_max,
            "autoDailyLossLimitSol": self.auto_daily_loss_limit_sol,
            "autoMaxConsecutiveLosses": self.auto_max_consecutive_losses,
            "reserveSolBalance": self.reserve_sol_balance,
            "positionSizingMode": self.position_sizing_mode,
            "privateKeyConfigured": bool(self.private_key_sol),
            "apiTokenConfigured": bool(self.api_token),
        }


def load_settings() -> Settings:
    settings = Settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
