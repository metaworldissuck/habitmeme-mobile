from __future__ import annotations

import math
import re
from typing import Any


NON_MEME_HINTS = {
    "protocol",
    "wrapped",
    "bridged",
    "staking",
    "liquid",
    "usd",
    "usdc",
    "usdt",
    "btc",
    "eth",
    "xstock",
    "stock",
    "yield",
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()
def response_data(payload: dict[str, Any]) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def response_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = response_data(payload)
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return [item for item in data["list"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def response_item(payload: dict[str, Any]) -> dict[str, Any]:
    data = response_data(payload)
    if isinstance(data, dict) and isinstance(data.get("list"), list) and data["list"]:
        first = data["list"][0]
        if isinstance(first, dict):
            return first
    if isinstance(data, dict):
        return data
    return {}


def nested_values(payload: Any) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)):
                pairs.append((key, float(value)))
            else:
                pairs.extend(nested_values(value))
    elif isinstance(payload, list):
        for item in payload:
            pairs.extend(nested_values(item))
    return pairs


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def contract_is_sol_native(contract: str) -> bool:
    return contract == ""


def is_meme_like_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("chain") != "sol":
        return False
    text = normalize_text(f"{candidate.get('symbol', '')} {candidate.get('name', '')}")
    return not any(term in text for term in NON_MEME_HINTS)


def merge_rankings(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not is_meme_like_candidate(item):
                continue
            contract = str(item.get("contract", "")).strip()
            if not contract or contract in merged:
                if contract in merged:
                    existing_sources = merged[contract].get("sources", [])
                    new_sources = item.get("sources", [])
                    merged[contract]["sources"] = sorted(
                        {str(source) for source in [*existing_sources, *new_sources] if source}
                    )
                continue
            clone = dict(item)
            clone["sources"] = sorted({str(source) for source in item.get("sources", []) if source})
            merged[contract] = clone
    return list(merged.values())


def shallow_score(candidate: dict[str, Any]) -> float:
    sources = {str(source) for source in candidate.get("sources", [])}

    turnover = as_float(candidate.get("turnover_24h"))
    change_24h = as_float(candidate.get("change_24h"))
    market_cap = as_float(candidate.get("market_cap"))

    score = 0.0
    score += 2.5 if "Hotpicks" in sources else 0.0
    score += 2.0 if "topGainers" in sources else 0.0
    score += 1.5 if len(sources) >= 2 else 0.0
    score += 2.0 if candidate.get("risk_level") == "low" else 0.0
    score += min(max(change_24h, -0.5), 2.0) * 2.0
    score += math.log10(turnover + 1.0)
    if 100_000 <= market_cap <= 25_000_000:
        score += 2.0
    return score


def rank_candidates(candidates: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=shallow_score, reverse=True)
    return ranked[:limit]


def liquidity_value(token_info: dict[str, Any], liquidity: dict[str, Any]) -> float:
    direct = as_float(token_info.get("liquidity"))
    if direct > 0:
        return direct

    items = response_list(liquidity)
    if items:
        guesses = []
        for item in items:
            guesses.extend(
                [
                    as_float(item.get("liquidity")),
                    as_float(item.get("liquidityUsd")),
                    as_float(item.get("reserveUsd")),
                    as_float(item.get("poolLiquidity")),
                    as_float(item.get("totalLiquidity")),
                ]
            )
        best = max(guesses, default=0.0)
        if best > 0:
            return best

    item = response_item(liquidity)
    guesses = [value for key, value in nested_values(item) if "liquid" in key.lower()]
    return max(guesses, default=0.0)


def tx_activity_value(tx_info: dict[str, Any], candidate: dict[str, Any]) -> float:
    guesses = []
    for key, value in nested_values(response_data(tx_info)):
        lowered = key.lower()
        if "24" in lowered and ("turnover" in lowered or "volume" in lowered):
            guesses.append(value)
    if guesses:
        return max(guesses)
    return as_float(candidate.get("turnover_24h"))


def security_summary(security: dict[str, Any]) -> dict[str, Any]:
    item = response_item(security)
    return {
        "high_risk": bool(item.get("highRisk")),
        "cannot_sell_all": bool(item.get("cannotSellAll")),
        "risk_count": int(as_float(item.get("riskCount"))),
        "warn_count": int(as_float(item.get("warnCount"))),
        "buy_tax": as_float(item.get("buyTax")),
        "sell_tax": as_float(item.get("sellTax")),
        "freeze_auth": bool(item.get("freezeAuth")),
        "mint_auth": bool(item.get("mintAuth")),
        "lp_lock": bool(item.get("lpLock")),
        "top_10_holder_risk_level": as_float(item.get("top_10_holder_risk_level")),
    }


def social_link_count(token: dict[str, Any]) -> int:
    return sum(1 for key in ("twitter", "telegram", "website") if str(token.get(key, "")).strip())


def tx_pressure_summary(tx_info: dict[str, Any]) -> dict[str, float]:
    summary = {
        "buy_volume_24h": 0.0,
        "sell_volume_24h": 0.0,
        "buyers_24h": 0.0,
        "sellers_24h": 0.0,
        "buy_volume_5m": 0.0,
        "sell_volume_5m": 0.0,
    }
    for key, value in nested_values(response_data(tx_info)):
        lowered = key.lower()
        if "24" in lowered and "buy" in lowered and ("turnover" in lowered or "volume" in lowered):
            summary["buy_volume_24h"] = max(summary["buy_volume_24h"], value)
        if "24" in lowered and "sell" in lowered and ("turnover" in lowered or "volume" in lowered):
            summary["sell_volume_24h"] = max(summary["sell_volume_24h"], value)
        if "24" in lowered and ("buyer" in lowered or ("buy" in lowered and "count" in lowered)):
            summary["buyers_24h"] = max(summary["buyers_24h"], value)
        if "24" in lowered and ("seller" in lowered or ("sell" in lowered and "count" in lowered)):
            summary["sellers_24h"] = max(summary["sellers_24h"], value)
        if "5" in lowered and "buy" in lowered and ("turnover" in lowered or "volume" in lowered):
            summary["buy_volume_5m"] = max(summary["buy_volume_5m"], value)
        if "5" in lowered and "sell" in lowered and ("turnover" in lowered or "volume" in lowered):
            summary["sell_volume_5m"] = max(summary["sell_volume_5m"], value)
    return summary


def analyze_candidate(
    candidate: dict[str, Any],
    *,
    token_info: dict[str, Any],
    security: dict[str, Any],
    liquidity: dict[str, Any],
    tx_info: dict[str, Any],
    rules: dict[str, float | int],
) -> dict[str, Any]:
    token = response_item(token_info)
    sec = security_summary(security)
    liq = liquidity_value(token, liquidity)
    activity = tx_activity_value(tx_info, candidate)
    tx_pressure = tx_pressure_summary(tx_info)
    holders = as_float(token.get("holders"))
    sources = {str(source) for source in candidate.get("sources", [])}
    top10_holder_percent = as_float(token.get("top10_holder_percent"))
    insider_holder_percent = as_float(token.get("insider_holder_percent"))
    sniper_holder_percent = as_float(token.get("sniper_holder_percent"))
    dev_holder_percent = as_float(token.get("dev_holder_percent"))
    dev_rug_percent = as_float(token.get("dev_rug_percent"))
    lock_lp_percent = as_float(token.get("lock_lp_percent"))
    social_count = social_link_count(token)
    buy_volume_24h = tx_pressure["buy_volume_24h"]
    sell_volume_24h = tx_pressure["sell_volume_24h"]
    buyers_24h = tx_pressure["buyers_24h"]
    sellers_24h = tx_pressure["sellers_24h"]
    buy_pressure_ratio = (buy_volume_24h - sell_volume_24h) / max(buy_volume_24h + sell_volume_24h, 1.0)
    buyer_ratio = (buyers_24h - sellers_24h) / max(buyers_24h + sellers_24h, 1.0)
    avg_buy_size = buy_volume_24h / max(buyers_24h, 1.0)

    blocked_reasons: list[str] = []
    warnings: list[str] = []

    if sec["high_risk"]:
        blocked_reasons.append("security.highRisk")
    if sec["cannot_sell_all"]:
        blocked_reasons.append("security.cannotSellAll")
    if sec["buy_tax"] > 10 or sec["sell_tax"] > 10:
        blocked_reasons.append("security.tax>10")
    elif sec["buy_tax"] > 5 or sec["sell_tax"] > 5:
        warnings.append("security.tax>5")
    if liq and liq < float(rules["min_liquidity_usd"]):
        blocked_reasons.append(f"liquidity<{int(float(rules['min_liquidity_usd']))}")
    elif liq and liq < max(float(rules["min_liquidity_usd"]) * 2.0, 25_000.0):
        warnings.append("liquidity.thin")
    if len(sources) < int(rules.get("min_source_count", 1)):
        blocked_reasons.append(f"sources<{int(rules.get('min_source_count', 1))}")
    if holders and holders < float(rules["min_holders"]):
        warnings.append(f"holders<{int(float(rules['min_holders']))}")
    if sec["risk_count"] > 0:
        warnings.append(f"security.riskCount={sec['risk_count']}")
    if sec["freeze_auth"]:
        blocked_reasons.append("security.freezeAuth")
    if sec["mint_auth"]:
        warnings.append("security.mintAuth")
    if not sec["lp_lock"] and lock_lp_percent <= 0:
        warnings.append("lp.unlocked")
    if top10_holder_percent > float(rules["max_top10_holder_percent"]):
        blocked_reasons.append("holders.top10")
    elif top10_holder_percent > float(rules["max_top10_holder_percent"]) * 0.75:
        warnings.append("holders.top10.elevated")
    if insider_holder_percent > float(rules["max_insider_holder_percent"]):
        blocked_reasons.append("holders.insider")
    elif insider_holder_percent > float(rules["max_insider_holder_percent"]) * 0.75:
        warnings.append("holders.insider.elevated")
    if sniper_holder_percent > float(rules["max_sniper_holder_percent"]):
        blocked_reasons.append("holders.sniper")
    elif sniper_holder_percent > float(rules["max_sniper_holder_percent"]) * 0.75:
        warnings.append("holders.sniper.elevated")
    if dev_holder_percent > float(rules["max_dev_holder_percent"]):
        blocked_reasons.append("dev.holder>limit")
    if dev_rug_percent >= float(rules["max_dev_rug_percent"]):
        blocked_reasons.append("dev.rugHistory")
    if social_count < int(rules["min_social_links"]):
        warnings.append("community.lowSocials")
    if buy_volume_24h > 0 and buyers_24h <= 5:
        warnings.append("smartMoney.concentrated")
    if buy_pressure_ratio < -0.2:
        warnings.append("flow.sellPressure")

    narrative_score = 0.0
    source_ranks = candidate.get("source_ranks", {})
    hotpick_rank = as_float(source_ranks.get("Hotpicks"), 9999.0)
    top_gainer_rank = as_float(source_ranks.get("topGainers"), 9999.0)
    narrative_score += 4.0 if "Hotpicks" in sources else 0.0
    narrative_score += 3.0 if "topGainers" in sources else 0.0
    narrative_score += 2.0 if len(sources) >= 2 else 0.0
    if hotpick_rank < 9999:
        narrative_score += max(0.0, 2.5 - (hotpick_rank - 1.0) * 0.15)
    if top_gainer_rank < 9999:
        narrative_score += max(0.0, 2.0 - (top_gainer_rank - 1.0) * 0.12)
    narrative_score += math.log10(max(activity, 1.0))

    community_score = 0.0
    community_score += min(holders / 500.0, 4.0)
    community_score += social_count * 1.5
    community_score += 1.0 if str(token.get("telegram", "")).strip() else 0.0
    community_score += 1.0 if str(token.get("twitter", "")).strip() else 0.0
    community_score -= top10_holder_percent / 20.0
    community_score -= insider_holder_percent / 15.0
    community_score -= sniper_holder_percent / 15.0

    smart_money_score = 0.0
    smart_money_score += buy_pressure_ratio * 6.0
    smart_money_score += buyer_ratio * 3.0
    smart_money_score += min(math.log10(max(liq, 1.0)), 6.0)
    smart_money_score += min(tx_pressure["buy_volume_5m"] / max(tx_pressure["sell_volume_5m"], 1.0), 3.0)
    if avg_buy_size > 2_000 and buyers_24h < 20:
        smart_money_score += 1.0
    if sell_volume_24h > buy_volume_24h:
        smart_money_score -= 1.5

    score = narrative_score + community_score + smart_money_score
    score -= sec["risk_count"] * 2.5
    score -= sec["warn_count"] * 1.0
    score -= max(sec["buy_tax"], sec["sell_tax"]) / 5.0
    if blocked_reasons:
        score -= 100.0

    return {
        "candidate": candidate,
        "token": token,
        "security": sec,
        "liquidity_usd": liq,
        "activity_24h": activity,
        "holders": holders,
        "social_count": social_count,
        "top10_holder_percent": top10_holder_percent,
        "insider_holder_percent": insider_holder_percent,
        "sniper_holder_percent": sniper_holder_percent,
        "dev_holder_percent": dev_holder_percent,
        "dev_rug_percent": dev_rug_percent,
        "lock_lp_percent": lock_lp_percent,
        "buy_pressure_ratio": buy_pressure_ratio,
        "buyer_ratio": buyer_ratio,
        "sources": sorted(sources),
        "narrative_score": narrative_score,
        "community_score": community_score,
        "smart_money_score": smart_money_score,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "score": score,
    }


def choose_best_analysis(analyses: list[dict[str, Any]]) -> dict[str, Any] | None:
    viable = [item for item in analyses if not item["blocked_reasons"]]
    pool = viable or analyses
    if not pool:
        return None
    return max(pool, key=lambda item: item["score"])


def quote_item(payload: dict[str, Any]) -> dict[str, Any]:
    return response_item(payload)


def quote_feature_supported(quote: dict[str, Any], feature: str) -> bool:
    item = quote_item(quote)
    features = item.get("features", [])
    if not isinstance(features, list):
        return False
    return feature in features
