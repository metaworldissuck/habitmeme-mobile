from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.bgw_client import BgwClient, CircuitOpenError


class DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class BgwClientTests(unittest.TestCase):
    def test_rate_limit_retry_then_success(self) -> None:
        events = []
        client = BgwClient(api_key="k", api_secret="s", partner_code="p", event_sink=lambda **kwargs: events.append(kwargs))
        responses = [
            DummyResponse(429, headers={"Retry-After": "0"}),
            DummyResponse(200, payload={"status": 0, "data": {"ok": True}}),
        ]

        def fake_post(*args, **kwargs):
            return responses.pop(0)

        with patch.object(client.session, "post", side_effect=fake_post):
            payload = client.request(endpoint="rankings", path="/bgw-pro/market/v3/topRank/detail", body={"name": "Hotpicks"})

        self.assertTrue(payload["data"]["ok"])
        self.assertTrue(any(event["error_type"] == "rate_limited" for event in events))
        self.assertEqual(client.breaker.state, "closed")

    def test_circuit_opens_after_failures(self) -> None:
        client = BgwClient(api_key="k", api_secret="s", partner_code="p", event_sink=lambda **kwargs: None)
        response = DummyResponse(500, payload={}, text="boom")

        with patch.object(client.session, "post", return_value=response):
            for _ in range(3):
                try:
                    client.request(endpoint="rankings", path="/bgw-pro/market/v3/topRank/detail", body={"name": "Hotpicks"})
                except Exception:
                    pass

            with self.assertRaises(CircuitOpenError):
                client.request(endpoint="order-create", path="/bgw-pro/swapx/order/makeSwapOrder", body={})


if __name__ == "__main__":
    unittest.main()
