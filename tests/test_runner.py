from __future__ import annotations

import unittest

from backend.runner import Runner
from backend.strategy import StrategyDefaults, StrategyEngine


class CountingClient:
    def __init__(self) -> None:
        self.calls: dict[tuple[str, str], int] = {}

    def request(self, *, endpoint: str, path: str, body: dict, method_type: str = "read") -> dict:
        key = (endpoint, str(body))
        self.calls[key] = self.calls.get(key, 0) + 1
        if endpoint == "token-info":
            return {"status": 0, "data": {"list": [{"symbol": "AAA"}]}}
        if endpoint == "rankings":
            return {
                "status": 0,
                "data": {
                    "list": [
                        {"chain": "sol", "symbol": f"MEME{i}", "name": f"Meme {i}", "contract": f"contract-{i}", "turnover_24h": 1000 - i}
                        for i in range(6)
                    ]
                },
            }
        if endpoint == "security":
            return {"status": 0, "data": {"highRisk": False, "cannotSellAll": False, "riskCount": 0, "warnCount": 0, "buyTax": 0, "sellTax": 0, "freezeAuth": False, "mintAuth": False, "lpLock": True}}
        if endpoint == "liquidity":
            return {"status": 0, "data": {"liquidityUsd": 150_000}}
        if endpoint == "tx-info":
            return {"status": 0, "data": {"buyVolume24h": 100_000, "sellVolume24h": 20_000, "buyers24h": 80, "sellers24h": 30, "buyVolume5m": 8_000, "sellVolume5m": 2_000}}
        return {"status": 0, "data": {}}


class RunnerTests(unittest.TestCase):
    def test_token_reads_use_short_ttl_cache(self) -> None:
        client = CountingClient()
        runner = Runner(client)  # type: ignore[arg-type]

        runner.token_info("sol", "contract-1")
        runner.token_info("sol", "contract-1")
        runner.security("sol", "contract-1")
        runner.security("sol", "contract-1")
        runner.liquidity("sol", "contract-1")
        runner.liquidity("sol", "contract-1")
        runner.tx_info("sol", "contract-1")
        runner.tx_info("sol", "contract-1")

        self.assertEqual(sum(1 for key, count in client.calls.items() if key[0] == "token-info" for _ in range(count)), 1)
        self.assertEqual(sum(1 for key, count in client.calls.items() if key[0] == "security" for _ in range(count)), 1)
        self.assertEqual(sum(1 for key, count in client.calls.items() if key[0] == "liquidity" for _ in range(count)), 1)
        self.assertEqual(sum(1 for key, count in client.calls.items() if key[0] == "tx-info" for _ in range(count)), 1)

    def test_tx_info_cache_is_scoped_by_context(self) -> None:
        client = CountingClient()
        runner = Runner(client)  # type: ignore[arg-type]

        runner.tx_info("sol", "contract-1", context="discover")
        runner.tx_info("sol", "contract-1", context="discover")
        runner.tx_info("sol", "contract-1", context="position_monitor")

        self.assertEqual(sum(1 for key, count in client.calls.items() if key[0] == "tx-info" for _ in range(count)), 2)

    def test_discover_uses_three_token_info_reads_and_one_full_analysis(self) -> None:
        client = CountingClient()
        runner = Runner(client)  # type: ignore[arg-type]
        strategy = StrategyEngine(
            runner,
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

        result = strategy.discover("combined", risk_mode="normal")

        self.assertEqual(len(result["analyses"]), 1)
        token_info_calls = sum(1 for key, count in client.calls.items() if key[0] == "token-info" for _ in range(count))
        security_calls = sum(1 for key, count in client.calls.items() if key[0] == "security" for _ in range(count))
        liquidity_calls = sum(1 for key, count in client.calls.items() if key[0] == "liquidity" for _ in range(count))
        tx_info_calls = sum(1 for key, count in client.calls.items() if key[0] == "tx-info" for _ in range(count))
        self.assertEqual(token_info_calls, 3)
        self.assertEqual(security_calls, 1)
        self.assertEqual(liquidity_calls, 1)
        self.assertEqual(tx_info_calls, 1)


if __name__ == "__main__":
    unittest.main()
