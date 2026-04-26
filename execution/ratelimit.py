import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import requests

from config.settings import (
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRY_BACKOFF_MAX_SECONDS,
    RETRY_MAX_ATTEMPTS,
)
from logs.logger import get_logger

log = get_logger("ratelimit")
RATE_LIMIT_STATUS_CODES = {418, 429}
RETRYABLE_STATUS_CODES = RATE_LIMIT_STATUS_CODES | {500, 502, 503, 504}
CCXT_RETRYABLE_ERROR_NAMES = {
    "NetworkError",
    "RequestTimeout",
    "ExchangeNotAvailable",
    "DDoSProtection",
    "RateLimitExceeded",
    "ExchangeError",
}


@dataclass
class RetryDecision:
    should_retry: bool
    delay_seconds: float
    reason: str


class RetryExhaustedError(RuntimeError):
    pass


def parse_retry_after(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)

    text = str(value).strip()
    if not text:
        return None
    try:
        return max(float(text), 0.0)
    except ValueError:
        pass

    try:
        now = now or datetime.now(timezone.utc)
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max((parsed - now).total_seconds(), 0.0)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def compute_backoff_delay(
    attempt: int,
    *,
    retry_after: Optional[float] = None,
    base_seconds: float = RETRY_BACKOFF_BASE_SECONDS,
    max_seconds: float = RETRY_BACKOFF_MAX_SECONDS,
    jitter_ratio: float = 0.25,
) -> float:
    if retry_after is not None:
        return min(max(retry_after, 0.0), max_seconds)

    delay = min(max_seconds, base_seconds * (2 ** max(attempt - 1, 0)))
    if delay <= 0:
        return 0.0
    jitter = 1.0 + (random.random() * jitter_ratio)
    return min(delay * jitter, max_seconds)


def _extract_retry_after(exc: Exception) -> Optional[float]:
    headers = getattr(exc, "response_headers", None) or getattr(exc, "headers", None) or {}
    retry_after = headers.get("Retry-After") if isinstance(headers, dict) else None
    return parse_retry_after(retry_after)


def _extract_status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    text = str(exc or "")
    for code in RETRYABLE_STATUS_CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    return None


def _is_retryable_network_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    class_name = exc.__class__.__name__
    return class_name in CCXT_RETRYABLE_ERROR_NAMES


def classify_retryable_error(exc: Exception, attempt: int, max_attempts: int) -> RetryDecision:
    status_code = _extract_status_code(exc)
    retry_after = _extract_retry_after(exc)

    if status_code not in RETRYABLE_STATUS_CODES:
        if _is_retryable_network_error(exc):
            status_code = 503
        else:
            return RetryDecision(False, 0.0, f"error no reintentable: {exc}")

    if attempt >= max_attempts:
        return RetryDecision(False, 0.0, f"reintentos agotados tras status {status_code}")

    delay = compute_backoff_delay(attempt, retry_after=retry_after)
    reason = f"status {status_code}"
    if retry_after is not None:
        reason += f" con Retry-After={retry_after:.2f}s"
    return RetryDecision(True, delay, reason)


def call_with_retry(
    func: Callable[[], Any],
    *,
    operation: str,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - capa genérica de resiliencia
            last_error = exc
            decision = classify_retryable_error(exc, attempt, max_attempts)
            if not decision.should_retry:
                raise

            log.warning(
                f"{operation}: reintentando intento {attempt}/{max_attempts} en {decision.delay_seconds:.2f}s ({decision.reason})"
            )
            sleep_func(decision.delay_seconds)

    raise RetryExhaustedError(f"{operation}: agotados {max_attempts} reintentos") from last_error


def request_json(
    url: str,
    *,
    method: str = "GET",
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    session: Optional[Any] = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Any:
    client = session or requests

    def _do_request():
        response = client.request(method, url, params=params, headers=headers, timeout=timeout)
        if response.status_code in RETRYABLE_STATUS_CODES:
            error = requests.HTTPError(f"HTTP {response.status_code} for {url}")
            error.response = response
            error.status_code = response.status_code
            error.response_headers = dict(response.headers or {})
            raise error
        response.raise_for_status()
        payload = response.json()
        if payload is None:
            raise ValueError("Empty JSON payload")
        return payload

    return call_with_retry(
        _do_request,
        operation=f"HTTP {method.upper()} {url}",
        max_attempts=max_attempts,
        sleep_func=sleep_func,
    )
