import unittest

import pandas as pd

from backtest_btc_rr_fixed import (
    FixedRRConfig,
    _build_pivot_target,
    _build_stop_target,
    _long_signal,
    _monthly_wr,
    _position_size,
)


class FixedRRBacktestTests(unittest.TestCase):
    def test_build_stop_target_long_fixed_rr(self):
        sl, tp = _build_stop_target(entry=100.0, stop_distance=10.0, side="LONG", rr_ratio=2.0)
        self.assertEqual(sl, 90.0)
        self.assertEqual(tp, 120.0)

    def test_build_stop_target_short_fixed_rr(self):
        sl, tp = _build_stop_target(entry=100.0, stop_distance=10.0, side="SHORT", rr_ratio=2.0)
        self.assertEqual(sl, 110.0)
        self.assertEqual(tp, 80.0)

    def test_build_pivot_target(self):
        self.assertEqual(_build_pivot_target(1000.0, "LONG", 50.0), 1050.0)
        self.assertEqual(_build_pivot_target(1000.0, "SHORT", 50.0), 950.0)

    def test_position_size_risk_pct(self):
        qty, notional, risk = _position_size(
            equity=10_000.0,
            entry=100.0,
            stop_distance=5.0,
            risk_pct=0.01,
            max_notional_pct=0.50,
        )
        self.assertAlmostEqual(risk, 100.0, places=6)
        self.assertAlmostEqual(qty, 20.0, places=6)
        self.assertAlmostEqual(notional, 2000.0, places=6)

    def test_monthly_wr_aggregation(self):
        df = pd.DataFrame(
            [
                {"exit_time": "2026-01-10T00:00:00", "net_pnl": 10.0},
                {"exit_time": "2026-01-20T00:00:00", "net_pnl": -5.0},
                {"exit_time": "2026-02-10T00:00:00", "net_pnl": 3.0},
            ]
        )
        monthly = _monthly_wr(df)
        self.assertEqual(len(monthly), 2)
        jan = monthly[monthly["month"] == "2026-01"].iloc[0]
        feb = monthly[monthly["month"] == "2026-02"].iloc[0]
        self.assertEqual(int(jan["trades"]), 2)
        self.assertEqual(int(jan["wins"]), 1)
        self.assertAlmostEqual(float(jan["win_rate_pct"]), 50.0, places=6)
        self.assertEqual(int(feb["trades"]), 1)
        self.assertEqual(int(feb["wins"]), 1)
        self.assertAlmostEqual(float(feb["win_rate_pct"]), 100.0, places=6)

    def test_long_signal_with_relaxed_default_profile(self):
        cfg = FixedRRConfig(use_ict_proxy=True, ict_must_confirm=False, strict_m5_macd_cross=False)
        prev = pd.Series({"m5_macd": -0.1, "m5_macd_sig": 0.1})
        row = pd.Series(
            {
                "close": 99850.0,
                "pivot": 100000.0,
                "m15_rsi": 50.0,
                "h1_rsi": 55.0,
                "m15_macd": 0.5,
                "m15_macd_sig": 0.2,
                "h1_macd": 0.3,
                "h1_macd_sig": 0.1,
                "m5_macd": 0.2,
                "m5_macd_sig": 0.1,
                "m15_ict_bullish_recent": False,
            }
        )
        self.assertTrue(_long_signal(row, prev, cfg))


if __name__ == "__main__":
    unittest.main()
