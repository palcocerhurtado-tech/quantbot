import unittest

import pandas as pd

from backtest_btc_confluence import BTCConfluenceConfig, _long_entry_signal, _short_entry_signal


class BTCConfluenceSignalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = BTCConfluenceConfig()

    def test_long_signal_near_pivot_with_mtf_confluence(self):
        prev = pd.Series({"m5_macd": -1.0, "m5_macd_sig": -0.5})
        row = pd.Series(
            {
                "close": 99_980.0,
                "pivot": 100_000.0,
                "m15_rsi": 35.0,
                "h1_rsi": 45.0,
                "m15_macd": 1.0,
                "m15_macd_sig": 0.5,
                "h1_macd": 0.8,
                "h1_macd_sig": 0.6,
                "m5_macd": 0.1,
                "m5_macd_sig": 0.0,
                "m15_ict_bullish_recent": True,
            }
        )
        self.assertTrue(_long_entry_signal(row, prev, self.cfg))

    def test_short_signal_near_pivot_with_mtf_confluence(self):
        prev = pd.Series({"m5_macd": 1.0, "m5_macd_sig": 0.2})
        row = pd.Series(
            {
                "close": 100_020.0,
                "pivot": 100_000.0,
                "m15_rsi": 65.0,
                "h1_rsi": 60.0,
                "m15_macd": -1.0,
                "m15_macd_sig": -0.6,
                "h1_macd": -0.9,
                "h1_macd_sig": -0.4,
                "m5_macd": -0.1,
                "m5_macd_sig": 0.0,
                "m15_ict_bearish_recent": True,
            }
        )
        self.assertTrue(_short_entry_signal(row, prev, self.cfg))

    def test_no_signal_without_ict_filter(self):
        prev = pd.Series({"m5_macd": -1.0, "m5_macd_sig": -0.5})
        row = pd.Series(
            {
                "close": 99_980.0,
                "pivot": 100_000.0,
                "m15_rsi": 35.0,
                "h1_rsi": 45.0,
                "m15_macd": 1.0,
                "m15_macd_sig": 0.5,
                "h1_macd": 0.8,
                "h1_macd_sig": 0.6,
                "m5_macd": 0.1,
                "m5_macd_sig": 0.0,
                "m15_ict_bullish_recent": False,
            }
        )
        self.assertFalse(_long_entry_signal(row, prev, self.cfg))


if __name__ == "__main__":
    unittest.main()
