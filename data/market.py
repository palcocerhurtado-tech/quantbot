"""
data/market.py
==============
Acceso a datos de mercado vía API pública de Binance.
No necesita clave API para los endpoints de mercado.
"""

import time
import requests
import pandas as pd
from config.settings import BINANCE_API_BASE, QUOTE, TOP_N_SCAN
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
    Excluye pares de stablecoins y tokens apalancados (UP/DOWN/BULL/BEAR).
    """
    data = _get("/api/v3/ticker/24hr")

    STABLE_SUFFIXES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP"}
    EXCLUDED_TOKENS = {"UP", "DOWN", "BULL", "BEAR"}

    pairs = []
    for t in data:
        sym = t["symbol"]
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]
        if base in STABLE_SUFFIXES:
            continue
        if any(base.endswith(tok) for tok in EXCLUDED_TOKENS):
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
