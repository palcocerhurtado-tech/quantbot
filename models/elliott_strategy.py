"""
models/elliott_strategy.py
===========================
Estrategia Elliott Wave Proxy para el bot live.

Lógica (backtesteada 50 meses, PF 3.13, WR 63 %):
  - Detecta swing low confirmado (5 barras a cada lado)
  - Filtro de tendencia: EMA 50 > EMA 200 y precio > EMA200 × 0.97
  - Solo longs (sesgo alcista de BTC)
  - SL  = entrada − max(ATR_14 × 1.5,  precio × 0.5 %)
  - TP  = entrada + stop_dist × RR  (RR 1:2 por defecto)

Uso en el ciclo live:
    strategy = ElliottStrategy()
    signal   = strategy.get_signal(df_diario)
    # signal = {"signal": "BUY"|"HOLD", "confidence": 0.8,
    #            "entry": 84000, "sl": 81500, "tp": 89000, ...}
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta
from logs.logger import get_logger

log = get_logger("elliott_strategy")

SWING_N       = 5      # barras a cada lado para confirmar un swing
ATR_MULT      = 1.5    # multiplicador ATR para el stop
MIN_STOP_PCT  = 0.005  # stop mínimo = 0.5 % del precio
RR            = 2.0    # Ratio Riesgo/Beneficio
MIN_BARS      = 220    # mínimo de barras para que EMA 200 sea válida
CONFIDENCE    = 0.80   # confianza fija asignada a señales Elliott


class ElliottStrategy:
    """
    Generador de señales live basado en el proxy de Ondas de Elliott.

    El swing confirmado siempre tiene SWING_N barras de retraso.
    En timeframe diario eso equivale a 5 días de confirmación,
    lo cual es conservador pero elimina falsas señales.
    """

    def get_signal(self, df: pd.DataFrame) -> dict:
        """
        Analiza el DataFrame de barras diarias y devuelve la señal actual.

        Parámetros
        ----------
        df : DataFrame con columnas open/high/low/close/volume indexado por fecha

        Devuelve
        --------
        dict con claves:
            signal     : "BUY" o "HOLD"
            confidence : probabilidad asignada (0.0 – 1.0)
            entry      : precio de entrada (último close)
            sl         : stop loss calculado con ATR
            tp         : take profit calculado con RR
            stop_dist  : distancia del stop en USD
            rr         : ratio riesgo/beneficio usado
        """
        df = self._add_indicators(df.copy())

        if len(df) < MIN_BARS:
            log.debug(f"Elliott: datos insuficientes ({len(df)} barras, mínimo {MIN_BARS})")
            return self._hold()

        # La última barra cuyo swing se puede confirmar es SWING_N+1 antes del final
        check_idx = -(SWING_N + 1)
        row = df.iloc[check_idx]

        is_swing_low = bool(row.get("swing_low", False))
        uptrend      = bool(row.get("uptrend", False))
        above_200    = bool(row.get("above_ema200", False))

        if not (is_swing_low and uptrend and above_200):
            return self._hold()

        # Entrada al cierre de la última barra disponible
        last      = df.iloc[-1]
        entry     = float(last["close"])
        atr       = float(last["atr"])
        stop_dist = max(atr * ATR_MULT, entry * MIN_STOP_PCT)
        sl        = entry - stop_dist
        tp        = entry + stop_dist * RR

        log.info(
            f"Elliott BUY | entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} "
            f"stop_dist={stop_dist:.2f} RR=1:{RR}"
        )
        return {
            "signal":     "BUY",
            "confidence": CONFIDENCE,
            "entry":      round(entry, 2),
            "sl":         round(sl, 2),
            "tp":         round(tp, 2),
            "stop_dist":  round(stop_dist, 2),
            "rr":         RR,
        }

    # ── helpers ────────────────────────────────────────────────────────────

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        c, h, l = df["close"], df["high"], df["low"]
        n = SWING_N

        df["atr"]      = ta.volatility.average_true_range(h, l, c, window=14)
        df["ema_50"]   = ta.trend.ema_indicator(c, window=50)
        df["ema_200"]  = ta.trend.ema_indicator(c, window=200)

        # Swing low: mínimo de una ventana centrada de 2n+1 barras
        df["swing_low"]    = (c == c.rolling(2 * n + 1, center=True).min())
        df["uptrend"]      = df["ema_50"] > df["ema_200"]
        df["above_ema200"] = c > df["ema_200"] * 0.97

        return df.dropna()

    @staticmethod
    def _hold() -> dict:
        return {
            "signal": "HOLD", "confidence": 0.0,
            "entry": 0.0, "sl": 0.0, "tp": 0.0,
            "stop_dist": 0.0, "rr": RR,
        }
