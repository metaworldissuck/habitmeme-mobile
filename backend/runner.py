from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .bgw_client import BgwClient, BgwError


class ServiceError(RuntimeError):
    pass


logger = logging.getLogger("habitmeme.runner")

READ_CACHE_TTLS = {
    "token-info": {"default": 30.0},
    "security": {"default": 45.0},
    "liquidity": {"default": 10.0, "discover": 10.0, "position_monitor": 8.0},
    "tx-info": {"default": 10.0, "discover": 15.0, "position_monitor": 5.0},
}


class Runner:
    def __init__(self, client: BgwClient) -> None:
        self.client = client
        self.order_sign = Path(__file__).resolve().parent / "order_sign.py"
        self._read_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def _cache_ttl(self, endpoint: str, context: str) -> float:
        config = READ_CACHE_TTLS.get(endpoint, {})
        if isinstance(config, dict):
            return float(config.get(context, config.get("default", 0.0)))
        return float(config)

    def _cache_key(self, endpoint: str, body: dict[str, Any], context: str) -> tuple[str, str]:
        return endpoint, context + ":" + json.dumps(body, sort_keys=True, separators=(",", ":"))

    def _cached_request(self, *, endpoint: str, path: str, body: dict[str, Any], context: str = "default") -> dict[str, Any]:
        ttl = self._cache_ttl(endpoint, context)
        if ttl <= 0:
            logger.info("runner cache bypass endpoint=%s context=%s", endpoint, context)
            return self.client.request(endpoint=endpoint, path=path, body=body)
        key = self._cache_key(endpoint, body, context)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._read_cache.get(key)
            if cached and cached[0] > now:
                logger.info("runner cache hit endpoint=%s context=%s ttl_remaining=%.1fs", endpoint, context, cached[0] - now)
                return cached[1]
        logger.info("runner cache miss endpoint=%s context=%s ttl=%.1fs", endpoint, context, ttl)
        response = self.client.request(endpoint=endpoint, path=path, body=body)
        with self._cache_lock:
            self._read_cache[key] = (now + ttl, response)
        return response

    def rankings(self, name: str) -> dict[str, Any]:
        return self.client.request(endpoint="rankings", path="/bgw-pro/market/v3/topRank/detail", body={"name": name})

    def token_info(self, chain: str, contract: str) -> dict[str, Any]:
        result = self._cached_request(
            endpoint="token-info",
            path="/bgw-pro/market/v3/coin/batchGetBaseInfo",
            body={"list": [{"chain": chain, "contract": contract}]},
            context="default",
        )
        data = result.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("list"), list) and data["list"]:
            return {"data": data["list"][0], "status": result.get("status", 0)}
        return result

    def token_price(self, chain: str, contract: str) -> dict[str, Any]:
        return self.token_info(chain, contract)

    def security(self, chain: str, contract: str) -> dict[str, Any]:
        return self._cached_request(
            endpoint="security",
            path="/bgw-pro/market/v3/coin/security/audits",
            body={"list": [{"chain": chain, "contract": contract}], "source": "bg"},
            context="default",
        )

    def liquidity(self, chain: str, contract: str, *, context: str = "default") -> dict[str, Any]:
        return self._cached_request(
            endpoint="liquidity",
            path="/bgw-pro/market/v3/poolList",
            body={"chain": chain, "contract": contract},
            context=context,
        )

    def tx_info(self, chain: str, contract: str, *, context: str = "default") -> dict[str, Any]:
        return self._cached_request(
            endpoint="tx-info",
            path="/bgw-pro/market/v3/coin/getTxInfo",
            body={"chain": chain, "contract": contract},
            context=context,
        )

    def order_quote(
        self,
        *,
        from_chain: str,
        from_contract: str,
        to_chain: str,
        to_contract: str,
        amount: str,
        from_address: str,
        to_address: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "fromChain": from_chain,
            "fromContract": from_contract,
            "fromAmount": amount,
            "toChain": to_chain,
            "toContract": to_contract,
            "fromAddress": from_address,
        }
        if to_address:
            body["toAddress"] = to_address
        return self.client.request(endpoint="order-quote", path="/bgw-pro/swapx/order/getSwapPrice", body=body, method_type="write")

    def order_create(
        self,
        *,
        from_chain: str,
        from_contract: str,
        to_chain: str,
        to_contract: str,
        amount: str,
        from_address: str,
        market: str,
        feature: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "fromChain": from_chain,
            "fromContract": from_contract,
            "fromAmount": amount,
            "toChain": to_chain,
            "toContract": to_contract,
            "fromAddress": from_address,
            "toAddress": from_address,
            "market": market,
        }
        if feature:
            body["feature"] = feature
        return self.client.request(endpoint="order-create", path="/bgw-pro/swapx/order/makeSwapOrder", body=body, method_type="write")

    def order_submit(self, order_id: str, signed_txs: list[str]) -> dict[str, Any]:
        return self.client.request(
            endpoint="order-submit",
            path="/bgw-pro/swapx/order/submitSwapOrder",
            body={"orderId": order_id, "signedTxs": signed_txs},
            method_type="write",
        )

    def order_status(self, order_id: str) -> dict[str, Any]:
        return self.client.request(
            endpoint="order-status",
            path="/bgw-pro/swapx/order/getSwapOrder",
            body={"orderId": order_id},
        )

    def sign_order(self, order_response: dict[str, Any], *, private_key_sol: str) -> list[str]:
        command = [sys.executable, str(self.order_sign), "--order-json", json.dumps(order_response), "--private-key-sol", private_key_sol]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ServiceError(result.stderr.strip() or result.stdout.strip() or "order_sign failed")
        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            raise ServiceError("Invalid signer response")
        return [str(item) for item in payload]


def extract_tx_id(status_payload: dict[str, Any]) -> str:
    txs = status_payload.get("txs", [])
    if isinstance(txs, list):
        for entry in txs:
            if isinstance(entry, dict) and entry.get("txId"):
                return str(entry["txId"])
    data = status_payload.get("data", {})
    if isinstance(data, dict):
        txs = data.get("txs", [])
        if isinstance(txs, list):
            for entry in txs:
                if isinstance(entry, dict) and entry.get("txId"):
                    return str(entry["txId"])
    return ""
