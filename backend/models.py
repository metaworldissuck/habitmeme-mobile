from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TradeMode = Literal["paper", "semi_auto_live", "auto_live"]
RiskMode = Literal["conservative", "normal", "degen"]
RankingType = Literal["combined", "hotpicks", "top_gainers"]


class DiscoverRequest(BaseModel):
    rankingType: RankingType = "combined"
    riskMode: RiskMode = "normal"


class OrderPrepareRequest(BaseModel):
    side: Literal["buy", "sell"]
    tokenContract: str
    positionId: int | None = Field(default=None, gt=0)
    budgetSol: float | None = Field(default=None, gt=0)
    tokenAmount: float | None = Field(default=None, gt=0)
    walletAddress: str | None = None
    mode: TradeMode = "paper"


class OrderExecuteRequest(OrderPrepareRequest):
    clientTradeId: str | None = None
    signedTxs: list[str] = Field(default_factory=list)


class SettingsPayload(BaseModel):
    walletAddress: str | None = None
    defaultBudgetSol: float | None = Field(default=None, gt=0)
    budgetSolMax: float | None = Field(default=None, gt=0)
    defaultMode: TradeMode | None = None
    riskMode: RiskMode | None = None
    rankingType: RankingType | None = None
    minLiquidityUsd: float | None = Field(default=None, gt=0)
    stopLossPct: float | None = Field(default=None, gt=0)
    takeProfitCostBasisPct: float | None = Field(default=None, gt=0)
    takeProfitHalfPct: float | None = Field(default=None, gt=0)
    moonbagTriggerPct: float | None = Field(default=None, gt=0)
    moonbagFraction: float | None = Field(default=None, gt=0, le=1)
    maxHoldHours: float | None = Field(default=None, gt=0)
    timeExitMaxGainPct: float | None = Field(default=None, ge=0)
    discoverInterval: int | None = Field(default=None, gt=0)
    orderPollInterval: int | None = Field(default=None, gt=0)
    orderPollMax: int | None = Field(default=None, gt=0)
    autoDailyLossLimitSol: float | None = Field(default=None, gt=0)
    autoMaxConsecutiveLosses: int | None = Field(default=None, gt=0)
    reserveSolBalance: float | None = Field(default=None, ge=0)


class AutoStartRequest(BaseModel):
    rankingType: RankingType = "combined"
    budgetSol: float = Field(gt=0)
    riskMode: RiskMode = "normal"
    mode: Literal["auto_live"] = "auto_live"


class ApiMessage(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
