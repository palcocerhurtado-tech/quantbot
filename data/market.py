import hmac
import hashlib
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import (
    BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_BASE_URL,
    BINANCE_TIMEOUT, BINANCE_TIMEOUT_BULK, BINANCE_RECVWINDOW,
    RETRY_TOTAL, RETRY_CONNECT, RETRY_BACKOFF, CIRCUIT_COOLDOWN,
)
from logs.logger import get_logger

log = get_logger("market")

# ── HTTP session with automatic retry on transient errors ─────────────────────

def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_CONNECT,
        read=2,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    if BINANCE_API_KEY:
        session.headers["X-MBX-APIKEY"] = BINANCE_API_KEY
    return session

_session: requests.Session = _build_session()

# ── Circuit breaker ───────────────────────────────────────────────────────────
# When a connection/DNS error occurs, we open the circuit for CIRCUIT_COOLDOWN
# seconds to avoid hammering the API with requests that will all fail anyway.

_circuit_open_until: float = 0.0


def _is_circuit_open() -> bool:
    return time.monotonic() < _circuit_open_until


def _trip_circuit() -> None:
    global _circuit_open_until
    _circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN
    log.warning(
        f"Red no disponible — circuit breaker activo {CIRCUIT_COOLDOWN}s"
    )


# ── Signed-request helper ─────────────────────────────────────────────────────

def _sign(params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(
        BINANCE_SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**params, "signature": sig}


# ── Core request ──────────────────────────────────────────────────────────────

def _get(
    endpoint: str,
    params: dict | None = None,
    signed: bool = False,
    timeout=None,
):
    if _is_circuit_open():
        return None

    url = BINANCE_BASE_URL + endpoint
    params = dict(params or {})

    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = BINANCE_RECVWINDOW
        params = _sign(params)

    try:
        r = _session.get(url, params=params, timeout=timeout or BINANCE_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        log.error(f"Binance API error {endpoint}: {e}")
        _trip_circuit()
        return None
    except requests.exceptions.HTTPError as e:
        log.error(f"Binance API error {endpoint}: {e}")
        return None
    except Exception as e:
        log.error(f"Binance API error {endpoint}: {e}")
        return None


# ── Public market data ────────────────────────────────────────────────────────

def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    data = _get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(
        data,
        columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades",
            "taker_base", "taker_quote", "ignore",
        ],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


def fetch_ticker_24hr() -> list:
    data = _get("/api/v3/ticker/24hr", timeout=BINANCE_TIMEOUT_BULK)
    return data if isinstance(data, list) else []


def get_latest_price(symbol: str) -> float:
    data = _get("/api/v3/ticker/price", {"symbol": symbol})
    if not data:
        return 0.0
    return float(data.get("price", 0))


# ── Account (signed) ──────────────────────────────────────────────────────────

def fetch_account() -> dict:
    if not BINANCE_SECRET_KEY:
        return {}
    return _get("/api/v3/account", signed=True) or {}


def get_usdt_balance() -> float:
    try:
        account = fetch_account()
        for b in account.get("balances", []):
            if b["asset"] == "USDT":
                return float(b["free"])
    except Exception as e:
        log.error(f"Error cuenta Binance: {e}")
    return 0.0


# ── Universe selection ────────────────────────────────────────────────────────

def fetch_top_usdt_pairs(min_volume_usdt: float = 5_000_000, top_n: int = 20) -> list[str]:
    tickers = fetch_ticker_24hr()
    if not tickers:
        return []

    pairs = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            vol = float(t.get("quoteVolume", 0))
            if vol >= min_volume_usdt:
                pairs.append((symbol, vol))
        except (ValueError, TypeError):
            continue

    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:top_n]]
