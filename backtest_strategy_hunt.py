#!/usr/bin/env python3
"""
backtest_strategy_hunt.py
=========================
Compara múltiples estrategias de trading sobre 50 meses de BTC.
Objetivo: encontrar estrategias con Profit Factor >= 1.5

Motor:
  - Datos vía yfinance (BTC-USD, diario) — sin dependencia de Binance API
  - RR fijo configurable (default 1:2)
  - 1 % de riesgo por operación sobre capital actual
  - Stop = max(ATR * multiplicador, precio * mínimo_pct)
  - Comisión 0.1 % por lado (round-trip 0.2 %)
  - Una posición abierta a la vez por estrategia

Uso:
    python backtest_strategy_hunt.py
    python backtest_strategy_hunt.py --months 50 --rr 2.0 --risk 0.01 --target-pf 1.5
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import ta
import yfinance as yf

warnings.filterwarnings("ignore")


# ─── Configuración ─────────────────────────────────────────────────────────────

@dataclass
class Config:
    symbol: str       = "BTC-USD"
    months: int       = 50
    rr: float         = 2.0       # Ratio Riesgo/Beneficio fijo
    risk_pct: float   = 0.01      # 1 % del capital por trade
    initial_capital: float = 10_000.0
    commission: float = 0.001     # 0.1 % por lado
    atr_mult: float   = 1.5       # multiplicador ATR para calcular stop
    min_stop_pct: float = 0.005   # stop mínimo = 0.5 % del precio
    min_trades: int   = 20        # trades mínimos para considerar válida la estrategia
    target_pf: float  = 1.5       # Profit Factor objetivo


# ─── Descarga y preparación de datos ────────────────────────────────────────────

def fetch_data(cfg: Config) -> pd.DataFrame:
    end   = datetime.utcnow()
    start = end - timedelta(days=cfg.months * 31 + 60)  # margen extra para indicadores
    print(f"Descargando {cfg.symbol}  {start.date()} → {end.date()} (intervalo diario)...")

    df = pd.DataFrame()

    # Método 1: yf.Ticker().history() — más estable con versiones antiguas de yfinance
    try:
        ticker = yf.Ticker(cfg.symbol)
        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
        )
        if not df.empty:
            df.columns = [c.lower() for c in df.columns]
    except Exception as e:
        print(f"  [método 1 falló: {e}]")

    # Método 2: yf.download() como fallback
    if df.empty:
        try:
            df = yf.download(
                cfg.symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
        except Exception as e:
            print(f"  [método 2 falló: {e}]")

    # Método 3: periodo largo como string (compatibilidad con yfinance <0.2)
    if df.empty:
        try:
            ticker = yf.Ticker(cfg.symbol)
            df = ticker.history(period="max", interval="1d")
            df.columns = [c.lower() for c in df.columns]
            cutoff_m3 = end - timedelta(days=cfg.months * 31)
            df = df[df.index >= pd.Timestamp(cutoff_m3).tz_localize(df.index.tz)]
        except Exception as e:
            print(f"  [método 3 falló: {e}]")

    if df.empty:
        raise RuntimeError(
            f"No se pudieron descargar datos para {cfg.symbol}.\n"
            f"  • Actualiza yfinance:  pip install --upgrade yfinance\n"
            f"  • Comprueba conexión: curl https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
        )

    # Normalizar índice y columnas
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.rename(columns={"Stock Splits": "splits", "Dividends": "dividends"}, errors="ignore")
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise RuntimeError(f"Columna '{col}' no encontrada. Columnas disponibles: {list(df.columns)}")

    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df[df["volume"] > 0]

    # Recortar al periodo exacto solicitado
    cutoff = end - timedelta(days=cfg.months * 31)
    df = df[df.index >= pd.Timestamp(cutoff)]
    print(f"  → {len(df)} barras diarias  ({df.index[0].date()} – {df.index[-1].date()})")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # Volatilidad
    df["atr"]      = ta.volatility.average_true_range(h, l, c, window=14)
    df["bb_upper"] = ta.volatility.bollinger_hband(c, window=20, window_dev=2)
    df["bb_lower"] = ta.volatility.bollinger_lband(c, window=20, window_dev=2)
    df["bb_mid"]   = ta.volatility.bollinger_mavg(c, window=20)
    df["bb_pct"]   = ta.volatility.bollinger_pband(c, window=20, window_dev=2)

    # Momentum
    df["rsi"]      = ta.momentum.rsi(c, window=14)
    df["stoch_k"]  = ta.momentum.stoch(h, l, c, window=14, smooth_window=3)
    df["stoch_d"]  = ta.momentum.stoch_signal(h, l, c, window=14, smooth_window=3)
    df["roc_10"]   = ta.momentum.roc(c, window=10)

    # Tendencia — MACD
    df["macd"]      = ta.trend.macd(c, window_slow=26, window_fast=12)
    df["macd_sig"]  = ta.trend.macd_signal(c, window_slow=26, window_fast=12, window_sign=9)
    df["macd_diff"] = ta.trend.macd_diff(c, window_slow=26, window_fast=12, window_sign=9)

    # Tendencia — EMAs / SMAs
    for w in (9, 21, 50, 200):
        df[f"ema_{w}"] = ta.trend.ema_indicator(c, window=w)
    df["sma_20"]  = ta.trend.sma_indicator(c, window=20)
    df["sma_50"]  = ta.trend.sma_indicator(c, window=50)
    df["adx"]     = ta.trend.adx(h, l, c, window=14)

    # Volumen
    df["obv"]  = ta.volume.on_balance_volume(c, v)
    df["vwap"] = (c * v).rolling(20).sum() / v.rolling(20).sum()

    # Donchian (shift para evitar look-ahead)
    df["don_high_20"] = h.rolling(20).max().shift(1)
    df["don_low_20"]  = l.rolling(20).min().shift(1)
    df["don_high_10"] = h.rolling(10).max().shift(1)
    df["don_low_10"]  = l.rolling(10).min().shift(1)

    return df.dropna()


# ─── Motor de simulación con RR fijo ────────────────────────────────────────────

def simulate(
    df: pd.DataFrame,
    long_signals: pd.Series,
    short_signals: pd.Series,
    cfg: Config,
) -> dict:
    """
    Simula trades con stop/target calculados en la barra de entrada.

    Fórmulas:
        stop_dist   = max(ATR_14 * atr_mult,  precio_entrada * min_stop_pct)
        qty         = (capital * risk_pct) / stop_dist
        LONG:  SL = entry - stop_dist   |  TP = entry + stop_dist * RR
        SHORT: SL = entry + stop_dist   |  TP = entry - stop_dist * RR
        gross_pnl   = side * (exit - entry) * qty      # side +1 o -1
        fee         = (entry + exit) * qty * commission
        net_pnl     = gross_pnl - fee
    """
    capital    = cfg.initial_capital
    equity     = [capital]
    trades: List[dict] = []

    close  = df["close"].values
    high_  = df["high"].values
    low_   = df["low"].values
    atr_   = df["atr"].values
    dates  = df.index

    in_trade    = False
    side        = 0      # +1 long, -1 short
    entry       = 0.0
    sl          = 0.0
    tp          = 0.0
    qty         = 0.0
    entry_date  = None

    for i in range(len(df)):
        # ── 1. Gestión de posición abierta ──────────────────────────────
        if in_trade:
            hit_sl = (side ==  1 and low_[i]  <= sl) or (side == -1 and high_[i] >= sl)
            hit_tp = (side ==  1 and high_[i] >= tp) or (side == -1 and low_[i]  <= tp)

            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp
                gross = side * (exit_price - entry) * qty
                fee   = (entry + exit_price) * qty * cfg.commission
                net   = gross - fee
                capital += net
                trades.append({
                    "date":   entry_date,
                    "side":   "LONG" if side == 1 else "SHORT",
                    "entry":  entry,
                    "exit":   exit_price,
                    "type":   "SL" if hit_sl else "TP",
                    "pnl":    net,
                    "capital": capital,
                })
                in_trade = False

        # ── 2. Abrir nueva posición ──────────────────────────────────────
        if not in_trade:
            atr_val   = atr_[i] if not np.isnan(atr_[i]) and atr_[i] > 0 else close[i] * 0.02
            stop_dist = max(atr_val * cfg.atr_mult, close[i] * cfg.min_stop_pct)
            if stop_dist <= 0:
                equity.append(capital)
                continue

            if long_signals.iloc[i]:
                entry     = close[i]
                sl        = entry - stop_dist
                tp        = entry + stop_dist * cfg.rr
                qty       = (capital * cfg.risk_pct) / stop_dist
                side      = 1
                in_trade  = True
                entry_date = dates[i]
            elif short_signals.iloc[i]:
                entry     = close[i]
                sl        = entry + stop_dist
                tp        = entry - stop_dist * cfg.rr
                qty       = (capital * cfg.risk_pct) / stop_dist
                side      = -1
                in_trade  = True
                entry_date = dates[i]

        equity.append(capital)

    # ── Cerrar posición abierta al final ─────────────────────────────────
    if in_trade:
        exit_price = close[-1]
        gross = side * (exit_price - entry) * qty
        fee   = (entry + exit_price) * qty * cfg.commission
        net   = gross - fee
        capital += net
        trades.append({
            "date":   entry_date,
            "side":   "LONG" if side == 1 else "SHORT",
            "entry":  entry,
            "exit":   exit_price,
            "type":   "TIMEOUT",
            "pnl":    net,
            "capital": capital,
        })

    if not trades:
        return _empty_result(cfg)

    pnl_list     = [t["pnl"] for t in trades]
    winners      = [p for p in pnl_list if p > 0]
    losers       = [p for p in pnl_list if p <= 0]
    gross_profit = sum(winners) if winners else 0.0
    gross_loss   = abs(sum(losers)) if losers else 0.0
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    wr           = len(winners) / len(pnl_list)

    eq_arr   = np.array(equity)
    peak_arr = np.maximum.accumulate(eq_arr)
    max_dd   = float(((peak_arr - eq_arr) / peak_arr).max()) * 100

    return {
        "capital_final": round(capital, 2),
        "pnl":           round(capital - cfg.initial_capital, 2),
        "return_pct":    round((capital - cfg.initial_capital) / cfg.initial_capital * 100, 2),
        "trades":        len(trades),
        "win_rate":      round(wr * 100, 2),
        "profit_factor": round(pf, 4),
        "max_drawdown":  round(max_dd, 2),
        "gross_profit":  round(gross_profit, 2),
        "gross_loss":    round(gross_loss, 2),
        "expectancy":    round(float(np.mean(pnl_list)), 4),
        "trade_log":     trades,
    }


def _empty_result(cfg: Config) -> dict:
    return {
        "capital_final": cfg.initial_capital,
        "pnl": 0.0, "return_pct": 0.0, "trades": 0,
        "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0, "expectancy": 0.0,
        "trade_log": [],
    }


# ─── Estrategias ────────────────────────────────────────────────────────────────

StratFn = Callable[[pd.DataFrame], Tuple[pd.Series, pd.Series]]


def _bool(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


def strat_rsi_reversal(df: pd.DataFrame, oversold: float = 30, overbought: float = 70):
    """RSI cruza de vuelta desde zona extrema."""
    rsi = df["rsi"]
    long  = _bool((rsi.shift(1) < oversold)  & (rsi >= oversold))
    short = _bool((rsi.shift(1) > overbought) & (rsi <= overbought))
    return long, short


def strat_rsi_long_only(df: pd.DataFrame, oversold: float = 30):
    """Solo longs: RSI recupera desde sobreventa. BTC tiene sesgo alcista."""
    rsi  = df["rsi"]
    long = _bool((rsi.shift(1) < oversold) & (rsi >= oversold) & (df["close"] > df["ema_200"]))
    return long, pd.Series(False, index=df.index)


def strat_macd_cross_trend(df: pd.DataFrame):
    """MACD crossover solo en dirección de la tendencia (EMA 200)."""
    m, s, c = df["macd"], df["macd_sig"], df["close"]
    long  = _bool((m.shift(1) < s.shift(1)) & (m >= s) & (c > df["ema_200"]))
    short = _bool((m.shift(1) > s.shift(1)) & (m <= s) & (c < df["ema_200"]))
    return long, short


def strat_macd_cross_all(df: pd.DataFrame):
    """MACD crossover sin filtro de tendencia."""
    m, s = df["macd"], df["macd_sig"]
    long  = _bool((m.shift(1) < s.shift(1)) & (m >= s))
    short = _bool((m.shift(1) > s.shift(1)) & (m <= s))
    return long, short


def strat_rsi_macd_confluence(df: pd.DataFrame):
    """RSI en zona neutra + MACD histogram girando — confluencia doble."""
    rsi, diff = df["rsi"], df["macd_diff"]
    long  = _bool((rsi > 35) & (rsi < 55) & (diff > 0) & (diff.shift(1) <= 0))
    short = _bool((rsi > 45) & (rsi < 65) & (diff < 0) & (diff.shift(1) >= 0))
    return long, short


def strat_bollinger_reversal(df: pd.DataFrame):
    """Precio toca banda exterior y cierra de vuelta hacia la media."""
    c = df["close"]
    long  = _bool((c.shift(1) <= df["bb_lower"].shift(1)) & (c > df["bb_lower"]))
    short = _bool((c.shift(1) >= df["bb_upper"].shift(1)) & (c < df["bb_upper"]))
    return long, short


def strat_bollinger_long_only(df: pd.DataFrame):
    """Solo longs: toca banda inferior. Evita shorts en sesgo alcista de BTC."""
    c = df["close"]
    long = _bool(
        (c.shift(1) <= df["bb_lower"].shift(1))
        & (c > df["bb_lower"])
        & (c > df["ema_200"] * 0.92)
    )
    return long, pd.Series(False, index=df.index)


def strat_ema_cross_9_21(df: pd.DataFrame):
    """EMA 9/21 crossover."""
    f, s = df["ema_9"], df["ema_21"]
    long  = _bool((f.shift(1) < s.shift(1)) & (f >= s))
    short = _bool((f.shift(1) > s.shift(1)) & (f <= s))
    return long, short


def strat_ema_cross_21_50(df: pd.DataFrame):
    """EMA 21/50 crossover — marco temporal más amplio."""
    f, s = df["ema_21"], df["ema_50"]
    long  = _bool((f.shift(1) < s.shift(1)) & (f >= s))
    short = _bool((f.shift(1) > s.shift(1)) & (f <= s))
    return long, short


def strat_donchian_breakout(df: pd.DataFrame):
    """Ruptura del canal Donchian de 20 barras."""
    c = df["close"]
    long  = _bool(c > df["don_high_20"])
    short = _bool(c < df["don_low_20"])
    return long, short


def strat_stoch_reversal(df: pd.DataFrame):
    """Estocástico cruza desde zona extrema en dirección de la tendencia."""
    k, d = df["stoch_k"], df["stoch_d"]
    c    = df["close"]
    long  = _bool((k.shift(1) < 20) & (k >= 20) & (k > d) & (c > df["ema_50"]))
    short = _bool((k.shift(1) > 80) & (k <= 80) & (k < d) & (c < df["ema_50"]))
    return long, short


def strat_elliott_proxy(df: pd.DataFrame):
    """
    Proxy de Ondas de Elliott:
    Detecta swings usando máximo/mínimo en ventana de 5 barras.
    Entrada long en swing-low durante tendencia alcista (EMA50 > EMA200).
    Entrada short en swing-high durante tendencia bajista.
    """
    c = df["close"]
    n = 5
    swing_lo = _bool(c == c.rolling(2 * n + 1, center=True).min())
    swing_hi = _bool(c == c.rolling(2 * n + 1, center=True).max())
    uptrend  = df["ema_50"] > df["ema_200"]

    long  = _bool(swing_lo & uptrend)
    short = _bool(swing_hi & ~uptrend)
    return long, short


def strat_rsi_trend_pullback(df: pd.DataFrame):
    """
    Pullback en tendencia: RSI retrocede a 40-50 en tendencia alcista.
    Solo longs, orientado al sesgo alcista de BTC.
    """
    rsi  = df["rsi"]
    c    = df["close"]
    long = _bool(
        (c > df["ema_200"])
        & (df["ema_50"] > df["ema_200"])
        & (rsi.shift(1) < 48)
        & (rsi >= 48)
        & (rsi < 65)
    )
    return long, pd.Series(False, index=df.index)


def strat_breakout_pullback(df: pd.DataFrame):
    """
    Ruptura de máximo de 20 barras + pullback confirmado por RSI.
    Solo longs.
    """
    c   = df["close"]
    rsi = df["rsi"]
    broke_out  = c > df["don_high_20"]
    was_broken = broke_out.rolling(10).max().shift(1) == 1
    long = _bool(was_broken & (rsi >= 40) & (rsi < 55) & (c > df["ema_21"]))
    return long, pd.Series(False, index=df.index)


def strat_rsi_bb_long_only(df: pd.DataFrame):
    """RSI bajo + precio en/bajo banda inferior de Bollinger — doble confluencia long."""
    rsi = df["rsi"]
    c   = df["close"]
    long = _bool(
        (rsi < 38)
        & (c <= df["bb_lower"] * 1.005)
        & (c > df["ema_200"] * 0.88)
    )
    return long, pd.Series(False, index=df.index)


def strat_adx_trend(df: pd.DataFrame):
    """
    ADX > 25 (tendencia fuerte) + EMA crossover en su dirección.
    """
    adx  = df["adx"]
    f, s = df["ema_9"], df["ema_21"]
    trend_strong = adx > 25
    long  = _bool(trend_strong & (f.shift(1) < s.shift(1)) & (f >= s) & (df["close"] > df["ema_50"]))
    short = _bool(trend_strong & (f.shift(1) > s.shift(1)) & (f <= s) & (df["close"] < df["ema_50"]))
    return long, short


# ─── Catálogo de estrategias ─────────────────────────────────────────────────────

STRATEGIES: Dict[str, StratFn] = {
    "RSI_Reversal_30_70":      lambda df: strat_rsi_reversal(df, 30, 70),
    "RSI_Reversal_25_75":      lambda df: strat_rsi_reversal(df, 25, 75),
    "RSI_LongOnly_EMA200":     lambda df: strat_rsi_long_only(df, 30),
    "MACD_Cross_TrendFilter":  strat_macd_cross_trend,
    "MACD_Cross_Sin_Filtro":   strat_macd_cross_all,
    "RSI_MACD_Confluence":     strat_rsi_macd_confluence,
    "Bollinger_Reversal":      strat_bollinger_reversal,
    "Bollinger_LongOnly":      strat_bollinger_long_only,
    "EMA_Cross_9_21":          strat_ema_cross_9_21,
    "EMA_Cross_21_50":         strat_ema_cross_21_50,
    "Donchian_Breakout_20":    strat_donchian_breakout,
    "Stoch_Reversal_Trend":    strat_stoch_reversal,
    "Elliott_Proxy":           strat_elliott_proxy,
    "RSI_Trend_Pullback":      strat_rsi_trend_pullback,
    "Breakout_Pullback":       strat_breakout_pullback,
    "RSI_BB_LongOnly":         strat_rsi_bb_long_only,
    "ADX_Trend_EMA":           strat_adx_trend,
}


# ─── Informe detallado ────────────────────────────────────────────────────────────

def _monthly_wr(trade_log: List[dict]) -> str:
    if not trade_log:
        return "  (sin trades)"
    tdf = pd.DataFrame(trade_log)
    tdf["year"] = pd.to_datetime(tdf["date"]).dt.year
    rows = []
    for yr, grp in tdf.groupby("year"):
        wr  = (grp["pnl"] > 0).mean() * 100
        pnl = grp["pnl"].sum()
        rows.append(f"  {yr}: {len(grp):3d} trades  WR {wr:5.1f}%  PnL ${pnl:+,.2f}")
    return "\n".join(rows)


def print_summary(results: List[dict], cfg: Config) -> None:
    sep = "=" * 78

    print(f"\n{sep}")
    print(f"  RANKING COMPLETO — {cfg.symbol}  |  {cfg.months} meses  |  RR 1:{cfg.rr}  |  Riesgo {cfg.risk_pct*100:.0f}%")
    print(f"  {'Estrategia':<32} {'Trades':>6} {'WR%':>6} {'PF':>7} {'Ret%':>8} {'DD%':>6}")
    print(sep)

    results_sorted = sorted(results, key=lambda x: x["profit_factor"], reverse=True)
    for r in results_sorted:
        mark = " ◀ OBJETIVO" if r["profit_factor"] >= cfg.target_pf and r["trades"] >= cfg.min_trades else ""
        print(
            f"  {r['strategy']:<32} {r['trades']:>6} {r['win_rate']:>6.1f} "
            f"{r['profit_factor']:>7.3f} {r['return_pct']:>+8.1f} {r['max_drawdown']:>6.1f}{mark}"
        )

    # Top 5
    print(f"\n{sep}")
    print("  TOP 5 POR PROFIT FACTOR")
    print(sep)
    for r in results_sorted[:5]:
        print(
            f"\n  [{r['strategy']}]\n"
            f"    Trades: {r['trades']}  WR: {r['win_rate']:.1f}%  PF: {r['profit_factor']:.3f}\n"
            f"    Capital: ${r['capital_final']:,.2f}  Retorno: {r['return_pct']:+.1f}%  "
            f"Max DD: {r['max_drawdown']:.1f}%\n"
            f"    Expectancy/trade: ${r['expectancy']:.2f}  "
            f"Bruto ganado: ${r['gross_profit']:,.2f}  Bruto perdido: ${r['gross_loss']:,.2f}"
        )

    # Estrategias que cumplen el objetivo
    winners = [r for r in results_sorted if r["profit_factor"] >= cfg.target_pf and r["trades"] >= cfg.min_trades]
    print(f"\n{sep}")
    if winners:
        best = winners[0]
        print(f"  MEJOR ESTRATEGIA  (PF >= {cfg.target_pf}): {best['strategy']}")
        print(sep)
        print(f"  Trades totales : {best['trades']}")
        print(f"  Win rate       : {best['win_rate']:.2f} %")
        print(f"  Profit Factor  : {best['profit_factor']:.4f}")
        print(f"  Retorno total  : {best['return_pct']:+.2f} %")
        print(f"  Max Drawdown   : {best['max_drawdown']:.2f} %")
        print(f"  Capital final  : ${best['capital_final']:,.2f}  (inicio ${cfg.initial_capital:,.2f})")
        print(f"  Expectancy     : ${best['expectancy']:.4f} por trade")
        print(f"\n  Desglose anual:")
        print(_monthly_wr(best["trade_log"]))

        print(f"\n  FÓRMULAS USADAS:")
        print(f"    stop_dist  = max(ATR_14 × {cfg.atr_mult},  precio × {cfg.min_stop_pct*100:.1f}%)")
        print(f"    SL largo   = entrada - stop_dist")
        print(f"    TP largo   = entrada + stop_dist × {cfg.rr}  (RR 1:{cfg.rr})")
        print(f"    qty        = (capital × {cfg.risk_pct*100:.0f}%) / stop_dist")
        print(f"    PnL neto   = (salida − entrada) × qty − comisión")
        print(f"    Comisión   = {cfg.commission*100:.2f}% por lado")
    else:
        best = results_sorted[0] if results_sorted else None
        print(f"  Ninguna estrategia alcanzó PF >= {cfg.target_pf} con >= {cfg.min_trades} trades.")
        if best:
            print(f"  Mejor resultado: {best['strategy']}  PF = {best['profit_factor']:.3f}")
        print(f"\n  Sugerencias:")
        print(f"    • Prueba --rr 1.5 (PF >= 1.5 requiere solo ~43 % WR con RR 2)")
        print(f"    • Combina señales de las estrategias mejor puntuadas")
        print(f"    • Considera gestión de posiciones parciales (TP parcial + trailing)")
    print(sep)


# ─── Main ─────────────────────────────────────────────────────────────────────────

def run_all(cfg: Config) -> List[dict]:
    """Descarga datos, añade indicadores y evalúa todas las estrategias."""
    df_raw = fetch_data(cfg)
    df     = add_indicators(df_raw)
    print(f"  Barras con indicadores completos: {len(df)}")

    results = []
    print(f"\n  Evaluando {len(STRATEGIES)} estrategias...\n")
    for name, fn in STRATEGIES.items():
        try:
            long_sig, short_sig = fn(df)
            result = simulate(df, long_sig, short_sig, cfg)
            result["strategy"] = name
            results.append(result)
        except Exception as exc:
            print(f"  [ERROR] {name}: {exc}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Búsqueda de estrategias BTC con PF >= objetivo")
    parser.add_argument("--symbol",     default="BTC-USD",   help="Símbolo yfinance (default BTC-USD)")
    parser.add_argument("--months",     type=int,   default=50,    help="Meses de histórico (default 50)")
    parser.add_argument("--rr",         type=float, default=2.0,   help="Ratio riesgo/beneficio (default 2.0)")
    parser.add_argument("--risk",       type=float, default=0.01,  help="Riesgo por trade 0-1 (default 0.01 = 1%%)")
    parser.add_argument("--capital",    type=float, default=10000, help="Capital inicial (default 10000)")
    parser.add_argument("--target-pf",  type=float, default=1.5,   help="PF objetivo (default 1.5)")
    parser.add_argument("--commission", type=float, default=0.001, help="Comisión por lado (default 0.001 = 0.1%%)")
    args = parser.parse_args()

    cfg = Config(
        symbol=args.symbol,
        months=args.months,
        rr=args.rr,
        risk_pct=args.risk,
        initial_capital=args.capital,
        target_pf=args.target_pf,
        commission=args.commission,
    )

    print("=" * 78)
    print(f"  BÚSQUEDA DE ESTRATEGIAS — {cfg.symbol}  |  {cfg.months} MESES  |  RR 1:{cfg.rr}  |  Riesgo {cfg.risk_pct*100:.0f}%")
    print("=" * 78)

    results = run_all(cfg)
    print_summary(results, cfg)


if __name__ == "__main__":
    main()
