from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from data.market import fetch_ohlcv
from logs.logger import get_logger
from models.features import add_technical_features

log = get_logger("btc_backtest")


@dataclass
class BTCConfluenceConfig:
    symbol: str = "BTCUSDC"
    initial_capital: float = 10_000.0
    pivot_offset: float = 20.0
    risk_per_trade: float = 0.005
    max_notional_pct: float = 0.20
    atr_mult: float = 1.2
    fee_bps: float = 4.0
    max_hold_bars: int = 36
    lookback_5m: int = 1000
    lookback_15m: int = 1000
    lookback_1h: int = 1000
    ict_lookback_15m: int = 8
    rsi_15m_long_max: float = 40.0
    rsi_15m_short_min: float = 60.0
    rsi_1h_long_max: float = 50.0
    rsi_1h_short_min: float = 50.0


def _add_ict_proxy_features(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    out = df.copy()
    out["bullish_fvg"] = out["low"] > out["high"].shift(2)
    out["bearish_fvg"] = out["high"] < out["low"].shift(2)
    out["bullish_ob"] = (out["close"].shift(1) < out["open"].shift(1)) & (out["close"] > out["high"].shift(1))
    out["bearish_ob"] = (out["close"].shift(1) > out["open"].shift(1)) & (out["close"] < out["low"].shift(1))
    out["ict_bullish_recent"] = (out["bullish_fvg"] | out["bullish_ob"]).rolling(lookback, min_periods=1).max().astype(bool)
    out["ict_bearish_recent"] = (out["bearish_fvg"] | out["bearish_ob"]).rolling(lookback, min_periods=1).max().astype(bool)
    return out


def _load_timeframe_data(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    raw = fetch_ohlcv(symbol, interval=interval, period=limit)
    if raw.empty:
        return raw
    tech = add_technical_features(raw)
    return tech


def _prepare_dataset(cfg: BTCConfluenceConfig) -> pd.DataFrame:
    raw_15m = fetch_ohlcv(cfg.symbol, interval="15m", period=cfg.lookback_15m)
    if raw_15m.empty:
        return pd.DataFrame()

    df_5m = _load_timeframe_data(cfg.symbol, "5m", cfg.lookback_5m)
    df_15m = _load_timeframe_data(cfg.symbol, "15m", cfg.lookback_15m)
    df_1h = _load_timeframe_data(cfg.symbol, "1h", cfg.lookback_1h)
    if df_5m.empty or df_15m.empty or df_1h.empty:
        return pd.DataFrame()

    ict_15m = _add_ict_proxy_features(raw_15m, cfg.ict_lookback_15m)
    ict_cols = ["ict_bullish_recent", "ict_bearish_recent"]
    df_15m = df_15m.join(ict_15m[ict_cols], how="left")
    df_15m[ict_cols] = df_15m[ict_cols].fillna(False)

    daily = raw_15m.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    daily["pivot"] = (daily["high"].shift(1) + daily["low"].shift(1) + daily["close"].shift(1)) / 3.0
    pivot_map = daily["pivot"].dropna()

    base = pd.DataFrame(index=df_5m.index).copy()
    base["open"] = df_5m["open"]
    base["high"] = df_5m["high"]
    base["low"] = df_5m["low"]
    base["close"] = df_5m["close"]
    base["m5_rsi"] = df_5m["rsi"]
    base["m5_macd"] = df_5m["macd"]
    base["m5_macd_sig"] = df_5m["macd_sig"]
    base["m5_atr"] = df_5m["atr"]
    base["session"] = base.index.floor("D")
    base["pivot"] = base["session"].map(pivot_map)

    def _merge_asof(left: pd.DataFrame, right: pd.DataFrame, prefix: str) -> pd.DataFrame:
        l = left.reset_index().rename(columns={"index": "timestamp"})
        r = right.reset_index().rename(columns={"index": "timestamp"})
        rename_map = {
            "rsi": f"{prefix}_rsi",
            "macd": f"{prefix}_macd",
            "macd_sig": f"{prefix}_macd_sig",
            "ict_bullish_recent": f"{prefix}_ict_bullish_recent",
            "ict_bearish_recent": f"{prefix}_ict_bearish_recent",
        }
        cols = ["timestamp", "rsi", "macd", "macd_sig"]
        if "ict_bullish_recent" in r.columns and "ict_bearish_recent" in r.columns:
            cols.extend(["ict_bullish_recent", "ict_bearish_recent"])
        r = r[cols].rename(columns=rename_map)
        merged = pd.merge_asof(l.sort_values("timestamp"), r.sort_values("timestamp"), on="timestamp", direction="backward")
        return merged.set_index("timestamp")

    merged = _merge_asof(base, df_15m, "m15")
    merged = _merge_asof(merged, df_1h, "h1")
    merged = merged.drop(columns=["session"])
    merged = merged.dropna(subset=["pivot", "m5_rsi", "m5_macd", "m5_macd_sig", "m15_rsi", "m15_macd", "m15_macd_sig", "h1_rsi", "h1_macd", "h1_macd_sig", "m5_atr"])
    merged["m15_ict_bullish_recent"] = merged.get("m15_ict_bullish_recent", False).astype(bool)
    merged["m15_ict_bearish_recent"] = merged.get("m15_ict_bearish_recent", False).astype(bool)
    return merged


def _long_entry_signal(row: pd.Series, prev: pd.Series, cfg: BTCConfluenceConfig) -> bool:
    near_pivot = row["close"] <= (row["pivot"] - cfg.pivot_offset) and row["close"] >= (row["pivot"] - cfg.pivot_offset * 2.5)
    rsi_ok = row["m15_rsi"] <= cfg.rsi_15m_long_max and row["h1_rsi"] <= cfg.rsi_1h_long_max
    macd_trend_ok = row["m15_macd"] > row["m15_macd_sig"] and row["h1_macd"] > row["h1_macd_sig"]
    trigger_ok = row["m5_macd"] > row["m5_macd_sig"] and prev["m5_macd"] <= prev["m5_macd_sig"]
    ict_ok = bool(row.get("m15_ict_bullish_recent", False))
    return bool(near_pivot and rsi_ok and macd_trend_ok and trigger_ok and ict_ok)


def _short_entry_signal(row: pd.Series, prev: pd.Series, cfg: BTCConfluenceConfig) -> bool:
    near_pivot = row["close"] >= (row["pivot"] + cfg.pivot_offset) and row["close"] <= (row["pivot"] + cfg.pivot_offset * 2.5)
    rsi_ok = row["m15_rsi"] >= cfg.rsi_15m_short_min and row["h1_rsi"] >= cfg.rsi_1h_short_min
    macd_trend_ok = row["m15_macd"] < row["m15_macd_sig"] and row["h1_macd"] < row["h1_macd_sig"]
    trigger_ok = row["m5_macd"] < row["m5_macd_sig"] and prev["m5_macd"] >= prev["m5_macd_sig"]
    ict_ok = bool(row.get("m15_ict_bearish_recent", False))
    return bool(near_pivot and rsi_ok and macd_trend_ok and trigger_ok and ict_ok)


def run_btc_confluence_backtest(cfg: BTCConfluenceConfig) -> dict[str, Any]:
    df = _prepare_dataset(cfg)
    if df.empty or len(df) < 80:
        return {"ok": False, "reason": "No hay suficientes datos para backtest"}

    capital = float(cfg.initial_capital)
    fee_rate = cfg.fee_bps / 10_000.0
    position: Optional[dict[str, Any]] = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = [capital]
    peak_equity = capital
    max_drawdown = 0.0

    index = df.index.to_list()
    for i in range(1, len(index)):
        ts = index[i]
        prev = df.iloc[i - 1]
        row = df.iloc[i]

        if position is not None:
            position["bars_held"] += 1
            side = position["side"]
            exit_price = None
            exit_reason = ""

            if side == "LONG":
                sl_hit = row["low"] <= position["sl"]
                tp_hit = row["high"] >= position["tp"]
                if sl_hit and tp_hit:
                    exit_price = position["sl"]
                    exit_reason = "sl_and_tp_same_bar"
                elif sl_hit:
                    exit_price = position["sl"]
                    exit_reason = "stop_loss"
                elif tp_hit:
                    exit_price = position["tp"]
                    exit_reason = "pivot_take_profit"
            else:
                sl_hit = row["high"] >= position["sl"]
                tp_hit = row["low"] <= position["tp"]
                if sl_hit and tp_hit:
                    exit_price = position["sl"]
                    exit_reason = "sl_and_tp_same_bar"
                elif sl_hit:
                    exit_price = position["sl"]
                    exit_reason = "stop_loss"
                elif tp_hit:
                    exit_price = position["tp"]
                    exit_reason = "pivot_take_profit"

            if exit_price is None and position["bars_held"] >= cfg.max_hold_bars:
                exit_price = row["close"]
                exit_reason = "time_exit"

            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty if side == "LONG" else (position["entry"] - exit_price) * qty
                fees = fee_rate * (position["entry"] * qty + exit_price * qty)
                net = gross - fees
                capital += net
                trade = {
                    "entry_time": position["entry_time"],
                    "exit_time": ts,
                    "side": side,
                    "entry": position["entry"],
                    "exit": exit_price,
                    "qty": qty,
                    "gross_pnl": gross,
                    "fees": fees,
                    "net_pnl": net,
                    "rr_realized": net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0,
                    "exit_reason": exit_reason,
                }
                trades.append(trade)
                position = None

        if position is None:
            enter_side = None
            if _long_entry_signal(row, prev, cfg):
                enter_side = "LONG"
            elif _short_entry_signal(row, prev, cfg):
                enter_side = "SHORT"

            if enter_side is not None:
                entry = row["close"]
                atr = max(float(row["m5_atr"]), 1e-9)
                stop_distance = max(atr * cfg.atr_mult, cfg.pivot_offset * 0.5)
                if enter_side == "LONG":
                    sl = entry - stop_distance
                    tp = float(row["pivot"])
                    if tp <= entry or sl <= 0:
                        enter_side = None
                else:
                    sl = entry + stop_distance
                    tp = float(row["pivot"])
                    if tp >= entry:
                        enter_side = None

                if enter_side is not None:
                    risk_amount = max(capital * cfg.risk_per_trade, 0.0)
                    qty_by_risk = risk_amount / stop_distance if stop_distance > 0 else 0.0
                    max_notional = min(capital * cfg.max_notional_pct, capital * 0.95)
                    notional = min(qty_by_risk * entry, max_notional)
                    qty = notional / entry if entry > 0 else 0.0

                    if qty > 0 and risk_amount > 0:
                        position = {
                            "side": enter_side,
                            "entry_time": ts,
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "qty": qty,
                            "risk_amount": risk_amount,
                            "bars_held": 0,
                        }

        unrealized = 0.0
        if position is not None:
            if position["side"] == "LONG":
                unrealized = (row["close"] - position["entry"]) * position["qty"]
            else:
                unrealized = (position["entry"] - row["close"]) * position["qty"]
        equity = capital + unrealized
        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        max_drawdown = max(max_drawdown, dd)

    if position is not None:
        last_ts = index[-1]
        last_close = float(df.iloc[-1]["close"])
        qty = position["qty"]
        gross = (last_close - position["entry"]) * qty if position["side"] == "LONG" else (position["entry"] - last_close) * qty
        fees = fee_rate * (position["entry"] * qty + last_close * qty)
        net = gross - fees
        capital += net
        trades.append(
            {
                "entry_time": position["entry_time"],
                "exit_time": last_ts,
                "side": position["side"],
                "entry": position["entry"],
                "exit": last_close,
                "qty": qty,
                "gross_pnl": gross,
                "fees": fees,
                "net_pnl": net,
                "rr_realized": net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0,
                "exit_reason": "end_of_backtest",
            }
        )

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {
            "ok": True,
            "symbol": cfg.symbol,
            "trades": 0,
            "initial_capital": cfg.initial_capital,
            "final_capital": round(capital, 2),
            "return_pct": round(((capital / cfg.initial_capital) - 1.0) * 100.0, 2),
            "max_drawdown_pct": round(max_drawdown * 100.0, 2),
            "win_rate_pct": 0.0,
            "expectancy_quote": 0.0,
            "profit_factor": 0.0,
            "reason": "Sin operaciones con los filtros actuales",
        }

    wins = trades_df[trades_df["net_pnl"] > 0]
    losses = trades_df[trades_df["net_pnl"] <= 0]
    gross_profit = float(wins["net_pnl"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["net_pnl"].sum())) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    result = {
        "ok": True,
        "symbol": cfg.symbol,
        "trades": int(len(trades_df)),
        "initial_capital": float(cfg.initial_capital),
        "final_capital": round(capital, 2),
        "pnl_quote": round(capital - cfg.initial_capital, 2),
        "return_pct": round(((capital / cfg.initial_capital) - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(max_drawdown * 100.0, 2),
        "win_rate_pct": round((len(wins) / len(trades_df)) * 100.0, 2),
        "expectancy_quote": round(float(trades_df["net_pnl"].mean()), 4),
        "profit_factor": round(float(profit_factor), 4) if np.isfinite(profit_factor) else float("inf"),
        "avg_rr": round(float(trades_df["rr_realized"].mean()), 4),
        "fees_total": round(float(trades_df["fees"].sum()), 4),
        "exit_reasons": trades_df["exit_reason"].value_counts().to_dict(),
        "sample_trades": trades_df.tail(5).to_dict(orient="records"),
    }
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backtest experimental BTC con confluencias: "
            "pivot liquidity + RSI/MACD MTF + proxies ICT (FVG/OrderBlock)."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDC")
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--pivot-offset", type=float, default=20.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.005)
    parser.add_argument("--max-notional-pct", type=float, default=0.20)
    parser.add_argument("--atr-mult", type=float, default=1.2)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--max-hold-bars", type=int, default=36)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    cfg = BTCConfluenceConfig(
        symbol=args.symbol,
        initial_capital=args.initial_capital,
        pivot_offset=args.pivot_offset,
        risk_per_trade=args.risk_per_trade,
        max_notional_pct=args.max_notional_pct,
        atr_mult=args.atr_mult,
        fee_bps=args.fee_bps,
        max_hold_bars=args.max_hold_bars,
    )
    result = run_btc_confluence_backtest(cfg)
    print("\n" + "=" * 72)
    print("BACKTEST BTC — PIVOT + RSI/MACD MTF + ICT PROXIES")
    print("=" * 72)
    if not result.get("ok"):
        print(f"Error: {result.get('reason', 'fallo desconocido')}")
        return

    print(f"Símbolo: {result['symbol']}")
    print(f"Trades: {result['trades']}")
    print(f"Capital inicial: ${result['initial_capital']:,.2f}")
    print(f"Capital final:   ${result['final_capital']:,.2f}")
    print(f"PnL:             ${result.get('pnl_quote', 0.0):+,.2f} ({result['return_pct']:+.2f}%)")
    print(f"Win rate:        {result['win_rate_pct']:.2f}%")
    print(f"Expectancy:      ${result['expectancy_quote']:+.4f}")
    print(f"Profit factor:   {result['profit_factor']}")
    print(f"Avg RR:          {result['avg_rr']:+.4f}")
    print(f"Max drawdown:    {result['max_drawdown_pct']:.2f}%")
    print(f"Fees totales:    ${result['fees_total']:,.4f}")
    print(f"Exit reasons:    {result['exit_reasons']}")
    print("\nÚltimos trades:")
    for trade in result.get("sample_trades", []):
        print(
            f"- {trade['entry_time']} {trade['side']} -> {trade['exit_time']} "
            f"net=${trade['net_pnl']:+.2f} reason={trade['exit_reason']}"
        )


if __name__ == "__main__":
    main()
