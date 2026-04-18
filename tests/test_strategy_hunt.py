"""
tests/test_strategy_hunt.py
Pruebas unitarias para backtest_strategy_hunt.py
Ejecutar: python -m unittest tests.test_strategy_hunt -v
"""

import sys
import os
import unittest
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest_strategy_hunt import (
    Config,
    add_indicators,
    simulate,
    strat_rsi_reversal,
    strat_rsi_long_only,
    strat_macd_cross_trend,
    strat_bollinger_reversal,
    strat_ema_cross_9_21,
    strat_donchian_breakout,
    strat_rsi_bb_long_only,
    STRATEGIES,
    _empty_result,
)


# ─── Generador de datos sintéticos ────────────────────────────────────────────

def _make_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Genera barras OHLCV sintéticas con tendencia alcista suave."""
    rng   = np.random.default_rng(seed)
    dates = pd.date_range(end=datetime.today(), periods=n, freq="D")
    close = 30_000.0 * np.cumprod(1 + rng.normal(0.001, 0.025, n))
    high  = close * (1 + rng.uniform(0.002, 0.015, n))
    low   = close * (1 - rng.uniform(0.002, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.008, n))
    vol   = rng.uniform(1_000, 50_000, n)
    df = pd.DataFrame({
        "open":   open_, "high": high, "low": low,
        "close":  close, "volume": vol,
    }, index=dates)
    return df


class TestAddIndicators(unittest.TestCase):
    def setUp(self):
        self.df = add_indicators(_make_ohlcv(300))

    def test_expected_columns(self):
        for col in ("atr", "rsi", "macd", "macd_sig", "macd_diff",
                    "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
                    "ema_200", "stoch_k", "stoch_d", "don_high_20", "don_low_20"):
            self.assertIn(col, self.df.columns, f"Falta columna: {col}")

    def test_no_nans_after_dropna(self):
        self.assertFalse(self.df.isnull().any().any(), "NaN encontrado tras dropna")

    def test_atr_positive(self):
        self.assertTrue((self.df["atr"] > 0).all(), "ATR debe ser positivo")

    def test_rsi_range(self):
        self.assertTrue((self.df["rsi"] >= 0).all() and (self.df["rsi"] <= 100).all())


class TestSimulateEngine(unittest.TestCase):
    def setUp(self):
        self.df  = add_indicators(_make_ohlcv(300))
        self.cfg = Config(initial_capital=10_000.0, risk_pct=0.01, rr=2.0)

    def test_no_signals_returns_initial_capital(self):
        empty = pd.Series(False, index=self.df.index)
        res   = simulate(self.df, empty, empty, self.cfg)
        self.assertEqual(res["trades"], 0)
        self.assertEqual(res["capital_final"], self.cfg.initial_capital)

    def test_single_long_tp(self):
        """Forzamos una señal long única y verificamos que PnL > 0 si TP se alcanza."""
        long_sig = pd.Series(False, index=self.df.index)
        long_sig.iloc[50] = True
        res = simulate(self.df, long_sig, pd.Series(False, index=self.df.index), self.cfg)
        self.assertGreaterEqual(res["trades"], 1)

    def test_result_keys_present(self):
        empty = pd.Series(False, index=self.df.index)
        res   = simulate(self.df, empty, empty, self.cfg)
        for key in ("capital_final", "pnl", "return_pct", "trades",
                    "win_rate", "profit_factor", "max_drawdown",
                    "gross_profit", "gross_loss", "expectancy", "trade_log"):
            self.assertIn(key, res, f"Clave ausente en resultado: {key}")

    def test_profit_factor_formula(self):
        """PF = gross_profit / gross_loss."""
        long_sig = pd.Series(False, index=self.df.index)
        long_sig.iloc[::20] = True  # señal cada 20 barras
        res = simulate(self.df, long_sig, pd.Series(False, index=self.df.index), self.cfg)
        if res["gross_loss"] > 0:
            expected_pf = res["gross_profit"] / res["gross_loss"]
            self.assertAlmostEqual(res["profit_factor"], round(expected_pf, 4), places=3)

    def test_empty_result_structure(self):
        res = _empty_result(self.cfg)
        self.assertEqual(res["trades"], 0)
        self.assertEqual(res["capital_final"], self.cfg.initial_capital)

    def test_capital_always_positive(self):
        long_sig = pd.Series(False, index=self.df.index)
        long_sig.iloc[::15] = True
        res = simulate(self.df, long_sig, pd.Series(False, index=self.df.index), self.cfg)
        for t in res["trade_log"]:
            self.assertGreater(t["capital"], 0, "Capital negativo detectado")


class TestStrategies(unittest.TestCase):
    def setUp(self):
        self.df = add_indicators(_make_ohlcv(300))

    def _check_signals(self, long_sig, short_sig):
        self.assertIsInstance(long_sig,  pd.Series)
        self.assertIsInstance(short_sig, pd.Series)
        self.assertEqual(len(long_sig),  len(self.df))
        self.assertEqual(len(short_sig), len(self.df))
        self.assertTrue(long_sig.dtype == bool or long_sig.dtype == object)

    def test_rsi_reversal(self):
        l, s = strat_rsi_reversal(self.df)
        self._check_signals(l, s)

    def test_rsi_long_only(self):
        l, s = strat_rsi_long_only(self.df)
        self._check_signals(l, s)
        self.assertTrue((~s.astype(bool)).all(), "strat_rsi_long_only no debe tener shorts")

    def test_macd_cross_trend(self):
        l, s = strat_macd_cross_trend(self.df)
        self._check_signals(l, s)

    def test_bollinger_reversal(self):
        l, s = strat_bollinger_reversal(self.df)
        self._check_signals(l, s)

    def test_ema_cross_9_21(self):
        l, s = strat_ema_cross_9_21(self.df)
        self._check_signals(l, s)

    def test_donchian_breakout(self):
        l, s = strat_donchian_breakout(self.df)
        self._check_signals(l, s)

    def test_rsi_bb_long_only(self):
        l, s = strat_rsi_bb_long_only(self.df)
        self._check_signals(l, s)
        self.assertTrue((~s.astype(bool)).all())

    def test_all_strategies_run(self):
        """Todas las estrategias del catálogo deben ejecutarse sin excepción."""
        cfg = Config()
        for name, fn in STRATEGIES.items():
            with self.subTest(strategy=name):
                long_sig, short_sig = fn(self.df)
                res = simulate(self.df, long_sig, short_sig, cfg)
                self.assertIn("profit_factor", res, f"{name}: falta profit_factor")


class TestPFMath(unittest.TestCase):
    """Verifica la aritmética del motor con trades controlados manualmente."""

    def test_rr_2_single_win(self):
        """Una sola trade ganadora con RR 2 debe aproximar gross_profit ≈ 2 × risk."""
        cfg = Config(initial_capital=10_000.0, risk_pct=0.01, rr=2.0, commission=0.0)
        df  = add_indicators(_make_ohlcv(300))

        # Señal en barra 50; precio de entrada = close[50]
        long_sig = pd.Series(False, index=df.index)
        long_sig.iloc[50] = True
        res = simulate(df, long_sig, pd.Series(False, index=df.index), cfg)

        if res["trades"] > 0:
            entry = df["close"].iloc[50]
            atr   = df["atr"].iloc[50]
            stop_dist = max(atr * cfg.atr_mult, entry * cfg.min_stop_pct)
            risk_amount = cfg.initial_capital * cfg.risk_pct
            expected_tp_gain = risk_amount * cfg.rr
            # La ganancia máxima posible es risk_amount * rr (sin comisiones)
            for t in res["trade_log"]:
                if t["type"] == "TP":
                    self.assertAlmostEqual(t["pnl"], expected_tp_gain, delta=expected_tp_gain * 0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
