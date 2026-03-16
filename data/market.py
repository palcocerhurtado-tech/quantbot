import yfinance as yf
import pandas as pd
from config.settings import SYMBOLS, INTERVAL, LOOKBACK
from logs.logger import get_logger

log = get_logger("market")

def fetch_ohlcv(symbol: str, interval: str = INTERVAL, period: str = LOOKBACK) -> pd.DataFrame:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        if df.empty:
            log.warning(f"Sin datos para {symbol}")
            return pd.DataFrame()
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index.name = "timestamp"
        df = df.dropna()
        df = df[df["volume"] > 0]
        log.info(f"{symbol}: {len(df)} filas descargadas")
        return df
    except Exception as e:
        log.error(f"Error descargando {symbol}: {e}")
        return pd.DataFrame()

def fetch_all_symbols(symbols: list = SYMBOLS) -> dict:
    data = {}
    for sym in symbols:
        df = fetch_ohlcv(sym)
        if not df.empty:
            data[sym] = df
    return data

def get_latest_price(symbol: str) -> float:
    try:
        return float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        log.error(f"Error precio {symbol}: {e}")
        return 0.0
