"""
data/market.py
==============
Acceso a datos de mercado vía API pública de Binance.
No necesita clave API para los endpoints de mercado.
get_account_snapshot() sí requiere API key (endpoints privados).
"""

import hashlib
import hmac
import time
from datetime import datetime
import requests
import pandas as pd
from config.settings import (
    BINANCE_API_BASE, QUOTE, TOP_N_SCAN,
    BINANCE_API_KEY, BINANCE_SECRET_KEY,
)
from logs.logger import get_logger

log = get_logger("market")

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

TIMEOUT = 10  # segundos por request


# ── Endpoints ────────────────────────────────────────────────────────────────

def _get(path: str, params=None):
    url = BINANCE_API_BASE + path
    try:
        r = _session.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Binance API error {path}: {e}")
        raise


# ── Universo dinámico ────────────────────────────────────────────────────────

def get_top_pairs_by_volume(quote: str = QUOTE, top_n: int = TOP_N_SCAN) -> list:
    """
    Devuelve los top_n pares de Binance ordenados por volumen en quote_asset (24h).
    Excluye stablecoins, tokens apalancados y tokens con caracteres no-ASCII.
    """
    data = _get("/api/v3/ticker/24hr")

    STABLE_BASE = {
        "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD",
        "USD1", "USDD", "USDX", "GUSD", "PYUSD", "FRAX",
    }
    EXCLUDED_SUFFIX = {"UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S"}

    pairs = []
    for t in data:
        sym = t["symbol"]
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]

        # Excluir stablecoins
        if base in STABLE_BASE:
            continue
        # Excluir tokens apalancados
        if any(base.endswith(s) for s in EXCLUDED_SUFFIX):
            continue
        # Excluir tokens con caracteres no-ASCII (spam/scam chinos, etc.)
        if not base.isascii() or not base.isalnum():
            continue
        # Excluir pares con precio irrealmente bajo (evita tokens basura)
        price = float(t.get("lastPrice", 0))
        if price <= 0:
            continue

        vol = float(t.get("quoteVolume", 0))
        if vol <= 0:
            continue
        pairs.append((sym, vol))

    pairs.sort(key=lambda x: x[1], reverse=True)
    result = [sym for sym, _ in pairs[:top_n]]
    log.info(f"Universo: top {top_n} pares por volumen {quote} → {result[:5]}...")
    return result


# ── Datos OHLCV ──────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """
    Descarga velas OHLCV desde Binance.

    Parámetros
    ----------
    symbol   : par de Binance, ej. "BTCUSDT"
    interval : "1m","5m","15m","30m","1h","2h","4h","1d"
    limit    : número de velas (máx 1000 por request)
    """
    data = _get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    if not data:
        log.warning(f"Sin datos para {symbol} {interval}")
        return pd.DataFrame()

    cols = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "n_trades",
        "taker_base", "taker_quote", "_ignore",
    ]
    df = pd.DataFrame(data, columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df[df["volume"] > 0]
    log.debug(f"{symbol} {interval}: {len(df)} velas descargadas")
    return df


# ── Precio actual ─────────────────────────────────────────────────────────────

def get_latest_price(symbol: str) -> float:
    """Devuelve el último precio del par en Binance."""
    try:
        data = _get("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"])
    except Exception as e:
        log.error(f"Error precio {symbol}: {e}")
        return 0.0


# ── Cuenta Binance (privado, requiere API key) ───────────────────────────────

def _signed_get(path: str, params=None) -> dict:
    """Request GET firmada con HMAC-SHA256 para endpoints privados de Binance."""
    params = dict(params or {})
    params["timestamp"]  = int(time.time() * 1000)
    params["recvWindow"] = 5000
    from urllib.parse import urlencode
    query = urlencode(params)
    sig   = hmac.new(
        BINANCE_SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256,
    ).hexdigest()
    params["signature"] = sig
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = _session.get(BINANCE_API_BASE + path, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_account_snapshot() -> dict:
    """
    Devuelve un resumen de la cuenta Binance Spot con saldos reales.

    Retorna dict con:
        balances   : lista de {asset, free, locked, price_usdt, value_usdt}
        total_usdt : valor total estimado en USDT
        updated_at : timestamp
    """
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        return {"error": "Sin API key configurada", "balances": [], "total_usdt": 0}

    try:
        data = _signed_get("/api/v3/account")
    except Exception as e:
        log.error(f"Error cuenta Binance: {e}")
        return {"error": str(e), "balances": [], "total_usdt": 0}

    # Filtrar activos con saldo > 0
    non_zero = [
        b for b in data.get("balances", [])
        if float(b["free"]) + float(b["locked"]) > 0
    ]

    # Obtener precios en USDT para cada activo
    rows = []
    total_usdt = 0.0
    for b in non_zero:
        asset  = b["asset"]
        free   = float(b["free"])
        locked = float(b["locked"])
        total  = free + locked

        if asset == "USDT":
            price_usdt = 1.0
        elif asset in ("USDC", "BUSD", "DAI", "TUSD"):
            price_usdt = 1.0
        else:
            price_usdt = get_latest_price(f"{asset}USDT")
            if price_usdt == 0:
                price_usdt = get_latest_price(f"{asset}BTC") * get_latest_price("BTCUSDT")

        value = total * price_usdt
        total_usdt += value
        rows.append({
            "asset":       asset,
            "free":        round(free, 8),
            "locked":      round(locked, 8),
            "total":       round(total, 8),
            "price_usdt":  round(price_usdt, 6),
            "value_usdt":  round(value, 4),
        })

    rows.sort(key=lambda x: x["value_usdt"], reverse=True)
    return {
        "balances":   rows,
        "total_usdt": round(total_usdt, 2),
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }


# ── Compatibilidad con código heredado ────────────────────────────────────────

def fetch_all_symbols(symbols: list) -> dict:
    result = {}
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym)
            if not df.empty:
                result[sym] = df
        except Exception as e:
            log.warning(f"fetch_all_symbols: {sym} falló — {e}")
    return result
