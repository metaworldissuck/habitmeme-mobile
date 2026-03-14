from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .legacy_strategy import (
    analyze_candidate,
    as_float,
    choose_best_analysis,
    quote_feature_supported,
    quote_item,
    shallow_score,
    social_link_count,
    response_item,
    response_list,
    is_meme_like_candidate,
)
from .runner import Runner


@dataclass(slots=True)
class StrategyDefaults:
    min_liquidity_usd: float
    stop_loss_pct: float
    take_profit_cost_basis_pct: float
    take_profit_half_pct: float
    moonbag_trigger_pct: float
    moonbag_fraction: float
    max_hold_hours: float
    time_exit_max_gain_pct: float
    max_open_positions: int = 1
    min_holders: int = 200
    min_social_links: int = 1
    max_top10_holder_percent: float = 55.0
    max_insider_holder_percent: float = 15.0
    max_sniper_holder_percent: float = 18.0
    max_dev_holder_percent: float = 8.0
    max_dev_rug_percent: float = 15.0

    def rules(self) -> dict[str, float | int]:
        return {
            "min_liquidity_usd": self.min_liquidity_usd,
            "min_holders": self.min_holders,
            "min_social_links": self.min_social_links,
            "max_top10_holder_percent": self.max_top10_holder_percent,
            "max_insider_holder_percent": self.max_insider_holder_percent,
            "max_sniper_holder_percent": self.max_sniper_holder_percent,
            "max_dev_holder_percent": self.max_dev_holder_percent,
            "max_dev_rug_percent": self.max_dev_rug_percent,
        }

    def profile(self, risk_mode: str) -> "StrategyProfile":
        normalized = risk_mode if risk_mode in {"conservative", "normal", "degen"} else "normal"
        if normalized == "conservative":
            return StrategyProfile(
                risk_mode=normalized,
                min_liquidity_usd=max(self.min_liquidity_usd * 1.5, self.min_liquidity_usd + 15_000.0),
                stop_loss_pct=min(self.stop_loss_pct, 0.10),
                take_profit_cost_basis_pct=0.8,
                take_profit_half_pct=3.0,
                moonbag_trigger_pct=7.0,
                moonbag_fraction=min(self.moonbag_fraction, 0.08),
                max_hold_hours=min(self.max_hold_hours, 18.0),
                time_exit_max_gain_pct=min(self.time_exit_max_gain_pct, 0.08),
                max_open_positions=min(self.max_open_positions, 2),
                min_holders=max(self.min_holders, 300),
                min_social_links=max(self.min_social_links, 2),
                max_top10_holder_percent=min(self.max_top10_holder_percent, 50.0),
                max_insider_holder_percent=min(self.max_insider_holder_percent, 12.0),
                max_sniper_holder_percent=min(self.max_sniper_holder_percent, 15.0),
                max_dev_holder_percent=min(self.max_dev_holder_percent, 6.0),
                max_dev_rug_percent=min(self.max_dev_rug_percent, 10.0),
                min_source_count=2,
                slot_budget_fraction=0.4,
            )
        if normalized == "degen":
            return StrategyProfile(
                risk_mode=normalized,
                min_liquidity_usd=max(self.min_liquidity_usd * 0.65, 30_000.0),
                stop_loss_pct=max(self.stop_loss_pct, 0.18),
                take_profit_cost_basis_pct=1.2,
                take_profit_half_pct=5.0,
                moonbag_trigger_pct=10.0,
                moonbag_fraction=max(self.moonbag_fraction, 0.15),
                max_hold_hours=max(self.max_hold_hours, 30.0),
                time_exit_max_gain_pct=max(self.time_exit_max_gain_pct, 0.18),
                max_open_positions=min(max(self.max_open_positions, 2), 2),
                min_holders=max(120, min(self.min_holders, 120)),
                min_social_links=1,
                max_top10_holder_percent=max(self.max_top10_holder_percent, 60.0),
                max_insider_holder_percent=max(self.max_insider_holder_percent, 18.0),
                max_sniper_holder_percent=max(self.max_sniper_holder_percent, 22.0),
                max_dev_holder_percent=max(self.max_dev_holder_percent, 9.0),
                max_dev_rug_percent=max(self.max_dev_rug_percent, 18.0),
                min_source_count=1,
                slot_budget_fraction=0.6,
            )
        return StrategyProfile(
            risk_mode=normalized,
            min_liquidity_usd=self.min_liquidity_usd,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_cost_basis_pct=1.0,
            take_profit_half_pct=4.0,
            moonbag_trigger_pct=9.0,
            moonbag_fraction=max(self.moonbag_fraction, 0.1),
            max_hold_hours=max(self.max_hold_hours, 24.0),
            time_exit_max_gain_pct=max(self.time_exit_max_gain_pct, 0.1),
            max_open_positions=min(max(self.max_open_positions, 2), 2),
            min_holders=self.min_holders,
            min_social_links=self.min_social_links,
            max_top10_holder_percent=self.max_top10_holder_percent,
            max_insider_holder_percent=self.max_insider_holder_percent,
            max_sniper_holder_percent=self.max_sniper_holder_percent,
            max_dev_holder_percent=self.max_dev_holder_percent,
            max_dev_rug_percent=self.max_dev_rug_percent,
            min_source_count=1,
            slot_budget_fraction=0.5,
        )


@dataclass(slots=True)
class StrategyProfile:
    risk_mode: str
    min_liquidity_usd: float
    stop_loss_pct: float
    take_profit_cost_basis_pct: float
    take_profit_half_pct: float
    moonbag_trigger_pct: float
    moonbag_fraction: float
    max_hold_hours: float
    time_exit_max_gain_pct: float
    max_open_positions: int
    min_holders: int
    min_social_links: int
    max_top10_holder_percent: float
    max_insider_holder_percent: float
    max_sniper_holder_percent: float
    max_dev_holder_percent: float
    max_dev_rug_percent: float
    min_source_count: int
    slot_budget_fraction: float

    def rules(self) -> dict[str, float | int]:
        return {
            "min_liquidity_usd": self.min_liquidity_usd,
            "min_holders": self.min_holders,
            "min_social_links": self.min_social_links,
            "max_top10_holder_percent": self.max_top10_holder_percent,
            "max_insider_holder_percent": self.max_insider_holder_percent,
            "max_sniper_holder_percent": self.max_sniper_holder_percent,
            "max_dev_holder_percent": self.max_dev_holder_percent,
            "max_dev_rug_percent": self.max_dev_rug_percent,
            "min_source_count": self.min_source_count,
        }


def determine_quote_feature(quote: dict[str, Any], amount_hint: float) -> str | None:
    if amount_hint < 0.05:
        return None
    if quote_feature_supported(quote, "no_gas"):
        return "no_gas"
    return None


def position_return_pct(position: dict[str, Any]) -> float:
    entry_price_sol = as_float(position.get("entry_price_sol"))
    current_price_sol = as_float(position.get("current_price_sol"))
    if entry_price_sol <= 0:
        return 0.0
    return (current_price_sol / entry_price_sol) - 1.0


def position_age_hours(position: dict[str, Any]) -> float:
    opened_at = position.get("opened_at") or position.get("updated_at")
    if not opened_at:
        return 0.0
    dt = datetime.fromisoformat(str(opened_at))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.0)


class StrategyEngine:
    def __init__(self, runner: Runner, defaults: StrategyDefaults) -> None:
        self.runner = runner
        self.defaults = defaults

    def discover(
        self,
        ranking_type: str,
        *,
        risk_mode: str = "normal",
        excluded_contracts: set[str] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        profile = self.defaults.profile(risk_mode)
        ranked = self._official_ranked_candidates(
            ranking_type,
            excluded_contracts=excluded_contracts,
            limit=max(limit, 3),
        )
        shortlist_size = 1
        coarse_screened = self._coarse_screen_candidates(ranked, shortlist_size=shortlist_size)
        analyses = []
        for coarse_candidate in coarse_screened:
            candidate = coarse_candidate["candidate"]
            contract = str(candidate.get("contract", "")).strip()
            token_info = coarse_candidate["token_info"]
            security = self.runner.security("sol", contract)
            liquidity = self.runner.liquidity("sol", contract, context="discover")
            tx_info = self.runner.tx_info("sol", contract, context="discover")
            analyses.append(
                analyze_candidate(
                    candidate,
                    token_info=token_info,
                    security=security,
                    liquidity=liquidity,
                    tx_info=tx_info,
                    rules=profile.rules(),
                )
            )
        analyses.sort(key=lambda item: as_float(item.get("score")), reverse=True)
        recommended = choose_best_analysis(analyses) if analyses else None
        return {"analyses": analyses, "recommended": recommended, "rankingType": ranking_type, "riskMode": profile.risk_mode}

    def _coarse_screen_candidates(self, ranked: list[dict[str, Any]], *, shortlist_size: int) -> list[dict[str, Any]]:
        coarse: list[dict[str, Any]] = []
        for candidate in ranked:
            contract = str(candidate.get("contract", "")).strip()
            token_info = self.runner.token_info("sol", contract)
            token = response_item(token_info)
            holders = as_float(token.get("holders"))
            social_count = social_link_count(token)
            top10_holder_percent = as_float(token.get("top10_holder_percent"))
            insider_holder_percent = as_float(token.get("insider_holder_percent"))
            dev_holder_percent = as_float(token.get("dev_holder_percent"))
            score = shallow_score(candidate)
            score += min(holders / 600.0, 3.0)
            score += social_count * 0.75
            score -= top10_holder_percent / 30.0
            score -= insider_holder_percent / 24.0
            score -= dev_holder_percent / 20.0
            coarse.append(
                {
                    "candidate": candidate,
                    "token_info": token_info,
                    "coarse_score": score,
                    "holders": holders,
                    "social_count": social_count,
                }
            )
        coarse.sort(key=lambda item: as_float(item.get("coarse_score")), reverse=True)
        return coarse[:shortlist_size]

    def _official_ranked_candidates(
        self,
        ranking_type: str,
        *,
        excluded_contracts: set[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        selected_sources: list[tuple[str, str]] = []
        normalized = ranking_type if ranking_type in {"combined", "hotpicks", "top_gainers"} else "combined"
        excluded = {item for item in (excluded_contracts or set()) if item}
        if normalized in {"combined", "hotpicks"}:
            selected_sources.append(("Hotpicks", "Hotpicks"))
        if normalized in {"combined", "top_gainers"}:
            selected_sources.append(("topGainers", "topGainers"))

        merged: dict[str, dict[str, Any]] = {}
        for source_label, endpoint_name in selected_sources:
            items = response_list(self.runner.rankings(endpoint_name))
            for index, item in enumerate(items, start=1):
                if not is_meme_like_candidate(item):
                    continue
                contract = str(item.get("contract", "")).strip()
                if not contract or contract in excluded:
                    continue
                existing = merged.get(contract)
                if existing is None:
                    clone = dict(item)
                    clone["sources"] = [source_label]
                    clone["source_ranks"] = {source_label: index}
                    merged[contract] = clone
                    continue
                sources = {str(source) for source in existing.get("sources", [])}
                sources.add(source_label)
                existing["sources"] = sorted(sources)
                source_ranks = dict(existing.get("source_ranks", {}))
                source_ranks[source_label] = min(index, int(source_ranks.get(source_label, index)))
                existing["source_ranks"] = source_ranks

        def sort_key(item: dict[str, Any]) -> tuple[int, int, int, float]:
            source_ranks = item.get("source_ranks", {})
            hotpick_rank = int(source_ranks.get("Hotpicks", 9999))
            top_gainer_rank = int(source_ranks.get("topGainers", 9999))
            combined_penalty = 0 if len(source_ranks) > 1 else 1
            if normalized == "hotpicks":
                primary_rank = hotpick_rank
            elif normalized == "top_gainers":
                primary_rank = top_gainer_rank
            else:
                primary_rank = min(hotpick_rank, top_gainer_rank)
            return (
                combined_penalty,
                primary_rank,
                hotpick_rank + top_gainer_rank,
                -as_float(item.get("turnover_24h")),
            )

        return sorted(merged.values(), key=sort_key)[:limit]

    def current_price_in_sol(self, token_contract: str, token_fallback_price_usd: float = 0.0) -> tuple[float, float]:
        sol_price_payload = self.runner.token_price("sol", "")
        sol_price_usd = as_float(response_item(sol_price_payload).get("price"))
        if token_contract == "":
            return 1.0, sol_price_usd
        token_payload = self.runner.token_info("sol", token_contract)
        token_price_usd = as_float(response_item(token_payload).get("price"), token_fallback_price_usd)
        if sol_price_usd <= 0:
            return 0.0, 0.0
        return token_price_usd / sol_price_usd, sol_price_usd

    def exit_signal_for_position(self, position: dict[str, Any], *, risk_mode: str = "normal") -> dict[str, Any] | None:
        if position.get("status") != "open":
            return None
        profile = self.defaults.profile(risk_mode)
        pnl_pct = position_return_pct(position)
        stage = position.get("take_profit_stage") or "entry"
        age_hours = position_age_hours(position)
        current_price_sol = as_float(position.get("current_price_sol"))
        entry_price_sol = as_float(position.get("entry_price_sol"))

        if pnl_pct <= -profile.stop_loss_pct:
            return {"reason": "stop_loss", "fraction": 1.0, "next_stage": stage, "pnl_pct": pnl_pct}
        if stage == "entry" and pnl_pct >= profile.take_profit_cost_basis_pct and current_price_sol > 0:
            fraction = min(entry_price_sol / current_price_sol, 1.0)
            return {"reason": "recover_cost_basis", "fraction": max(fraction, 0.05), "next_stage": "cost_basis_recovered", "pnl_pct": pnl_pct}
        if stage == "cost_basis_recovered" and pnl_pct >= profile.take_profit_half_pct:
            return {"reason": "take_profit_half", "fraction": 0.5, "next_stage": "half_taken", "pnl_pct": pnl_pct}
        if stage == "half_taken" and pnl_pct >= profile.moonbag_trigger_pct:
            return {"reason": "leave_moonbag", "fraction": max(1.0 - profile.moonbag_fraction, 0.0), "next_stage": "moonbag", "pnl_pct": pnl_pct}
        if age_hours >= profile.max_hold_hours and pnl_pct <= profile.time_exit_max_gain_pct:
            return {"reason": "time_exit", "fraction": 1.0, "next_stage": stage, "pnl_pct": pnl_pct}
        return None

    def summarize_quote(self, quote: dict[str, Any]) -> dict[str, Any]:
        item = quote_item(quote)
        return {
            "toAmount": as_float(item.get("toAmount")),
            "market": item.get("market", ""),
            "priceImpact": as_float(item.get("priceImpact")),
            "features": item.get("features", []),
        }
