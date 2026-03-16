
import pandas as pd
import numpy as np
import ta
from logs.logger import get_logger

log = get_logger("features")

def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """Añade 20+ indicadores técnicos al DataFrame de precios."""
    if df.empty or len(df) < 30:
        log.warning("Datos insuficientes para calcular features")
        return df

    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # ── Tendencia ──────────────────────────────────────────────────
    df["sma_10"]   = ta.trend.sma_indicator(c, window=10)
    df["sma_20"]   = ta.trend.sma_indicator(c, window=20)
    df["ema_12"]   = ta.trend.ema_indicator(c, window=12)
    df["ema_26"]   = ta.trend.ema_indicator(c, window=26)
    df["macd"]     = ta.trend.macd(c)
    df["macd_sig"] = ta.trend.macd_signal(c)
    df["macd_diff"]= ta.trend.macd_diff(c)

    # ── Momentum ───────────────────────────────────────────────────
    df["rsi"]      = ta.momentum.rsi(c, window=14)
    df["stoch_k"]  = ta.momentum.stoch(h, l, c, window=14)
    df["stoch_d"]  = ta.momentum.stoch_signal(h, l, c, window=14)
    df["roc"]      = ta.momentum.roc(c, window=10)

    # ── Volatilidad ────────────────────────────────────────────────
    df["bb_high"]  = ta.volatility.bollinger_hband(c, window=20)
    df["bb_low"]   = ta.volatility.bollinger_lband(c, window=20)
    df["bb_pct"]   = ta.volatility.bollinger_pband(c, window=20)
    df["atr"]      = ta.volatility.average_true_range(h, l, c, window=14)

    # ── Volumen ────────────────────────────────────────────────────
    df["obv"]      = ta.volume.on_balance_volume(c, v)
    df["vwap"]     = (c * v).cumsum() / v.cumsum()

    # ── Features derivadas ─────────────────────────────────────────
    df["return_1d"]  = c.pct_change(1)
    df["return_5d"]  = c.pct_change(5)
    df["price_vs_sma20"] = (c - df["sma_20"]) / df["sma_20"]
    df["volume_sma"] = v.rolling(20).mean()
    df["volume_ratio"] = v / df["volume_sma"]

    # ── Target: sube o baja mañana ─────────────────────────────────
    df["target"] = (c.shift(-1) > c).astype(int)

    df = df.dropna()
    log.info(f"Features calculadas: {len(df.columns)} columnas, {len(df)} filas")
    return df

def add_sentiment_features(df: pd.DataFrame, sentiment: dict) -> pd.DataFrame:
    """Añade scores de sentimiento como features al DataFrame."""
    df = df.copy()
    df["sent_compound"] = sentiment.get("compound", 0.0)
    df["sent_positive"] = sentiment.get("positive", 0.0)
    df["sent_negative"] = sentiment.get("negative", 0.0)
    return df

def get_feature_columns() -> list:
    """Lista de columnas que usa el modelo (sin target ni precios raw)."""
    return [
        "sma_10", "sma_20", "ema_12", "ema_26",
        "macd", "macd_sig", "macd_diff",
        "rsi", "stoch_k", "stoch_d", "roc",
        "bb_high", "bb_low", "bb_pct", "atr",
        "obv", "vwap", "return_1d", "return_5d",
        "price_vs_sma20", "volume_ratio",
        "sent_compound", "sent_positive", "sent_negative"
    ]
