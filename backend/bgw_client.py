from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


BASE_URL = "https://bopenapi.bgwapi.io"
DEFAULT_PARTNER_CODE = "bgw_swap_public"
DEFAULT_API_KEY = "4843D8C3F1E20772C0E634EDACC5C5F9A0E2DC92"
DEFAULT_API_SECRET = "F2ABFDC684BDC6775FD6286B8D06A3AAD30FD587"
READ_ENDPOINTS = {"rankings", "token-info", "token-price", "security", "liquidity", "tx-info", "order-status"}
ESSENTIAL_WHILE_OPEN = {"order-status"}
logger = logging.getLogger("habitmeme.bgw")


class BgwError(RuntimeError):
    pass


class CircuitOpenError(BgwError):
    pass


class RateLimitedError(BgwError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamRequestError(BgwError):
    pass


class UpstreamResponseError(BgwError):
    pass


@dataclass
class BreakerState:
    state: str = "closed"
    consecutive_failures: int = 0
    consecutive_429: int = 0
    open_until: float = 0.0


class BgwClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        partner_code: str,
        event_sink: Callable[..., None],
    ) -> None:
        self.api_key = api_key or DEFAULT_API_KEY
        self.api_secret = api_secret or DEFAULT_API_SECRET
        self.partner_code = partner_code or DEFAULT_PARTNER_CODE
        self.event_sink = event_sink
        self.session = requests.Session()
        self.breaker = BreakerState()
        self.lock = threading.Lock()

    def sign_request(self, api_path: str, body_str: str, timestamp: str) -> str:
        content = {
            "apiPath": api_path,
            "body": body_str,
            "x-api-key": self.api_key,
            "x-api-timestamp": timestamp,
        }
        payload = json.dumps(dict(sorted(content.items())), separators=(",", ":"))
        digest = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def request(self, *, endpoint: str, path: str, body: dict[str, Any], method_type: str = "read") -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            if self.breaker.state == "open" and now < self.breaker.open_until and endpoint not in ESSENTIAL_WHILE_OPEN:
                self.event_sink(
                    endpoint=endpoint,
                    http_status=None,
                    error_type="circuit_open",
                    retry_count=0,
                    breaker_state=self.breaker.state,
                    detail="Request blocked by circuit breaker",
                )
                raise CircuitOpenError("Circuit breaker open")
            if self.breaker.state == "open" and now >= self.breaker.open_until:
                self.breaker.state = "half-open"

        retries = 3 if method_type == "read" and endpoint in READ_ENDPOINTS else 1
        body_str = json.dumps(body, separators=(",", ":"), sort_keys=True) if body else ""
        url = BASE_URL + path

        for attempt in range(retries):
            attempt_label = f"{attempt + 1}/{retries}"
            logger.info(
                "bgw request start endpoint=%s attempt=%s/%s method_type=%s breaker=%s body=%s",
                endpoint,
                attempt + 1,
                retries,
                method_type,
                self.breaker.state,
                body_str[:300],
            )
            timestamp = str(int(time.time() * 1000))
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "x-api-timestamp": timestamp,
                "x-api-signature": self.sign_request(path, body_str, timestamp),
            }
            if "/swapx/" in path:
                headers["Partner-Code"] = self.partner_code

            try:
                response = self.session.post(url, data=body_str or None, headers=headers, timeout=(5, 20))
            except requests.RequestException as exc:
                logger.warning("bgw request network error endpoint=%s attempt=%s detail=%s", endpoint, attempt_label, str(exc))
                self._on_failure(endpoint, "network_unstable", attempt, str(exc))
                if attempt < retries - 1:
                    wait = self._backoff_seconds(attempt)
                    logger.info(
                        "bgw request retry scheduled endpoint=%s next_attempt=%s/%s wait=%.2fs reason=network_error",
                        endpoint,
                        attempt + 2,
                        retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise UpstreamRequestError(str(exc)) from exc

            if response.status_code == 429:
                logger.warning("bgw request rate limited endpoint=%s attempt=%s retry_after=%s", endpoint, attempt_label, self._retry_after(response))
                retry_after = self._retry_after(response)
                self._on_rate_limited(endpoint, attempt, retry_after, response.text[:400])
                if attempt < retries - 1:
                    wait = self._rate_limit_wait_seconds(retry_after, attempt)
                    logger.info(
                        "bgw request retry scheduled endpoint=%s next_attempt=%s/%s wait=%.2fs reason=rate_limited",
                        endpoint,
                        attempt + 2,
                        retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise RateLimitedError("Bitget API rate limited", retry_after=retry_after)

            if response.status_code >= 500:
                logger.warning("bgw request upstream error endpoint=%s status=%s attempt=%s", endpoint, response.status_code, attempt_label)
                self._on_failure(endpoint, f"http_{response.status_code}", attempt, response.text[:400])
                if attempt < retries - 1:
                    wait = self._backoff_seconds(attempt)
                    logger.info(
                        "bgw request retry scheduled endpoint=%s next_attempt=%s/%s wait=%.2fs reason=http_%s",
                        endpoint,
                        attempt + 2,
                        retries,
                        wait,
                        response.status_code,
                    )
                    time.sleep(wait)
                    continue
                raise UpstreamRequestError(f"HTTP {response.status_code}")

            if response.status_code >= 400:
                logger.warning("bgw request client error endpoint=%s status=%s attempt=%s body=%s", endpoint, response.status_code, attempt_label, response.text[:300])
                self.event_sink(
                    endpoint=endpoint,
                    http_status=response.status_code,
                    error_type="client_error",
                    retry_count=attempt,
                    breaker_state=self.breaker.state,
                    detail=response.text[:400],
                )
                raise UpstreamRequestError(f"HTTP {response.status_code}: {response.text[:200]}")

            try:
                payload = response.json()
            except ValueError as exc:
                logger.warning("bgw request invalid json endpoint=%s attempt=%s", endpoint, attempt_label)
                self._on_failure(endpoint, "invalid_json", attempt, response.text[:400])
                if attempt < retries - 1:
                    wait = self._backoff_seconds(attempt)
                    logger.info(
                        "bgw request retry scheduled endpoint=%s next_attempt=%s/%s wait=%.2fs reason=invalid_json",
                        endpoint,
                        attempt + 2,
                        retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise UpstreamResponseError("Invalid JSON response") from exc

            logger.info("bgw request success endpoint=%s status=%s attempt=%s", endpoint, response.status_code, attempt_label)
            self._on_success(endpoint, attempt, response.status_code)
            return payload

        raise UpstreamRequestError(f"Retries exhausted for {endpoint}")

    def _retry_after(self, response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _backoff_seconds(self, attempt: int) -> float:
        base = min(0.5 * (2**attempt), 8.0)
        return base + random.uniform(0.05, 0.25)

    def _rate_limit_wait_seconds(self, retry_after: float | None, attempt: int) -> float:
        if retry_after is not None:
            return retry_after
        if attempt == 0:
            return random.uniform(2.0, 4.0)
        if attempt == 1:
            return random.uniform(5.0, 10.0)
        return 12.0

    def _on_success(self, endpoint: str, attempt: int, http_status: int) -> None:
        with self.lock:
            self.breaker.consecutive_failures = 0
            self.breaker.consecutive_429 = 0
            self.breaker.state = "closed"
            self.breaker.open_until = 0.0
        self.event_sink(
            endpoint=endpoint,
            http_status=http_status,
            error_type="",
            retry_count=attempt,
            breaker_state=self.breaker.state,
            detail="success",
        )

    def _on_rate_limited(self, endpoint: str, attempt: int, retry_after: float | None, detail: str) -> None:
        with self.lock:
            self.breaker.consecutive_429 += 1
            if self.breaker.consecutive_429 >= 3:
                self.breaker.state = "open"
                self.breaker.open_until = time.monotonic() + max(retry_after or 15.0, 15.0)
        self.event_sink(
            endpoint=endpoint,
            http_status=429,
            error_type="rate_limited",
            retry_count=attempt,
            breaker_state=self.breaker.state,
            detail=detail,
        )

    def _on_failure(self, endpoint: str, error_type: str, attempt: int, detail: str) -> None:
        with self.lock:
            self.breaker.consecutive_failures += 1
            if self.breaker.consecutive_failures >= 3:
                self.breaker.state = "open"
                self.breaker.open_until = time.monotonic() + 20.0
        self.event_sink(
            endpoint=endpoint,
            http_status=None,
            error_type=error_type,
            retry_count=attempt,
            breaker_state=self.breaker.state,
            detail=detail,
        )
