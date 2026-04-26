from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from config.settings import BASE_DIR, BINANCE_BASE_URL
from execution.ratelimit import request_json
from logs.logger import get_logger
from models.features import add_technical_features

log = get_logger("btc_rr_backtest")

INTERVAL_TO_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass
class FixedRRConfig:
    symbol: str = "BTCUSDC"
    months: int = 50
    initial_capital: float = 10_000.0
    risk_pct: float = 0.01
    rr_ratio: float = 2.0
    tp_from_pivot: bool = False
    tp_pivot_distance: float = 0.0
    pivot_offset: float = 20.0
    pivot_offset_pct: float = 0.0015
    pivot_zone_mult: float = 8.0
    atr_mult: float = 1.2
    fixed_stop_distance: float = 0.0
    max_notional_pct: float = 0.25
    fee_bps: float = 4.0
    max_hold_bars: int = 48
    use_ict_proxy: bool = True
    ict_must_confirm: bool = False
    min_confluence: int = 2
    strict_m5_macd_cross: bool = False
    rsi_15m_long_max: float = 55.0
    rsi_15m_short_min: float = 45.0
    rsi_1h_long_max: float = 60.0
    rsi_1h_short_min: float = 40.0
    cache: bool = True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_stop_target(entry: float, stop_distance: float, side: str, rr_ratio: float) -> tuple[float, float]:
    if side == "LONG":
        sl = entry - stop_distance
        tp = entry + (rr_ratio * stop_distance)
    else:
        sl = entry + stop_distance
        tp = entry - (rr_ratio * stop_distance)
    return sl, tp


def _build_pivot_target(pivot: float, side: str, tp_pivot_distance: float) -> float:
    if side == "LONG":
        return pivot + tp_pivot_distance
    return pivot - tp_pivot_distance


def _position_size(
    equity: float,
    entry: float,
    stop_distance: float,
    risk_pct: float,
    max_notional_pct: float,
) -> tuple[float, float, float]:
    risk_amount = max(equity * risk_pct, 0.0)
    if stop_distance <= 0 or entry <= 0 or risk_amount <= 0:
        return 0.0, 0.0, 0.0

    qty_by_risk = risk_amount / stop_distance
    max_notional = max(equity * max_notional_pct, 0.0)
    qty_by_notional = max_notional / entry if entry > 0 else 0.0
    qty = min(qty_by_risk, qty_by_notional)
    if qty <= 0:
        return 0.0, 0.0, 0.0

    actual_notional = qty * entry
    actual_risk_amount = qty * stop_distance
    return qty, actual_notional, actual_risk_amount


def _cache_path(symbol: str, interval: str, start_ms: int, end_ms: int) -> Path:
    cache_dir = BASE_DIR / "logs" / "backtests"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{symbol}_{interval}_{start_ms}_{end_ms}.csv"


def _load_cache_csv(path: Path) -> pd.DataFrame:
    cached = pd.read_csv(path)
    if cached.empty:
        return pd.DataFrame()
    cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True).dt.tz_localize(None)
    cached = cached.set_index("timestamp")
    return cached[["open", "high", "low", "close", "volume"]].astype(float)


def _fallback_cache_path(symbol: str, interval: str) -> Optional[Path]:
    cache_dir = BASE_DIR / "logs" / "backtests"
    if not cache_dir.exists():
        return None
    candidates = sorted(
        cache_dir.glob(f"{symbol}_{interval}_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _fetch_klines_paginated(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    if interval not in INTERVAL_TO_MS:
        raise ValueError(f"Intervalo no soportado: {interval}")

    path = _cache_path(symbol, interval, start_ms, end_ms)
    if use_cache and path.exists():
        cached = _load_cache_csv(path)
        if not cached.empty:
            print(
                f"[cache] {symbol} {interval}: usando {len(cached)} velas desde {path}",
                flush=True,
            )
            return cached

    if use_cache and not path.exists():
        fallback = _fallback_cache_path(symbol, interval)
        if fallback is not None:
            cached = _load_cache_csv(fallback)
            if not cached.empty:
                print(
                    f"[cache] {symbol} {interval}: usando cache alternativo {fallback.name} "
                    f"({len(cached)} velas)",
                    flush=True,
                )
                return cached

    interval_ms = INTERVAL_TO_MS[interval]
    url = f"{BINANCE_BASE_URL}/klines"
    cursor = int(start_ms)
    rows: list[list[Any]] = []
    expected_rows = max(int((end_ms - start_ms) / max(interval_ms, 1)), 1)
    expected_batches = max(int(np.ceil(expected_rows / 1000.0)), 1)
    batch_count = 0
    start_label = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    end_label = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(
        f"[download] {symbol} {interval}: desde {start_label} hasta {end_label} "
        f"(~{expected_rows:,} velas, ~{expected_batches} lotes)",
        flush=True,
    )

    while cursor < end_ms:
        batch_count += 1
        try:
            batch = request_json(
                url,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error(f"Error descargando klines {symbol} {interval}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break

        rows.extend(batch)
        last_open = int(batch[-1][0])
        next_cursor = last_open + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if len(batch) < 1000:
            break

        if batch_count == 1 or batch_count % 20 == 0:
            progress_rows = len(rows)
            progress_pct = min((progress_rows / expected_rows) * 100.0, 100.0)
            print(
                f"[download] {symbol} {interval}: lote {batch_count}/{expected_batches} "
                f"velas={progress_rows:,} ({progress_pct:.1f}%)",
                flush=True,
            )

        time.sleep(0.03)

    if not rows:
        print(f"[download] {symbol} {interval}: sin datos descargados", flush=True)
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df = df.set_index("timestamp")
    out = df[["open", "high", "low", "close", "volume"]].astype(float)

    if use_cache:
        out.reset_index().to_csv(path, index=False)
        print(f"[cache] {symbol} {interval}: guardado en {path}", flush=True)
    print(f"[download] {symbol} {interval}: total velas {len(out):,}", flush=True)
    return out


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    return df.resample(rule, label="right", closed="right").agg(agg).dropna()


def _add_ict_proxy_15m(df_15m_raw: pd.DataFrame, lookback_bars: int = 8) -> pd.DataFrame:
    out = df_15m_raw.copy()
    out["bullish_fvg"] = out["low"] > out["high"].shift(2)
    out["bearish_fvg"] = out["high"] < out["low"].shift(2)
    out["bullish_ob"] = (out["close"].shift(1) < out["open"].shift(1)) & (out["close"] > out["high"].shift(1))
    out["bearish_ob"] = (out["close"].shift(1) > out["open"].shift(1)) & (out["close"] < out["low"].shift(1))
    out["ict_bullish_recent"] = (out["bullish_fvg"] | out["bullish_ob"]).rolling(lookback_bars, min_periods=1).max().astype(bool)
    out["ict_bearish_recent"] = (out["bearish_fvg"] | out["bearish_ob"]).rolling(lookback_bars, min_periods=1).max().astype(bool)
    return out[["ict_bullish_recent", "ict_bearish_recent"]]


def _merge_asof(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    l = left.reset_index().rename(columns={"index": "timestamp"})
    r = right.reset_index().rename(columns={"index": "timestamp"})
    r = r[["timestamp", *columns]]
    merged = pd.merge_asof(l.sort_values("timestamp"), r.sort_values("timestamp"), on="timestamp", direction="backward")
    return merged.set_index("timestamp")


def _prepare_mtf_dataset(cfg: FixedRRConfig, end_dt: datetime) -> pd.DataFrame:
    end_utc = end_dt.astimezone(timezone.utc)
    start_utc = end_utc - timedelta(days=int(cfg.months * 30.44))
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    raw_5m = _fetch_klines_paginated(cfg.symbol, "5m", start_ms, end_ms, use_cache=cfg.cache)
    if raw_5m.empty:
        return pd.DataFrame()

    raw_15m = _resample_ohlcv(raw_5m, "15min")
    raw_1h = _resample_ohlcv(raw_5m, "1h")
    if raw_15m.empty or raw_1h.empty:
        return pd.DataFrame()

    feat_5m = add_technical_features(raw_5m)
    feat_15m = add_technical_features(raw_15m)
    feat_1h = add_technical_features(raw_1h)
    if feat_5m.empty or feat_15m.empty or feat_1h.empty:
        return pd.DataFrame()

    if cfg.use_ict_proxy:
        ict_15m = _add_ict_proxy_15m(raw_15m)
        feat_15m = feat_15m.join(ict_15m, how="left")
        feat_15m["ict_bullish_recent"] = feat_15m["ict_bullish_recent"].fillna(False).astype(bool)
        feat_15m["ict_bearish_recent"] = feat_15m["ict_bearish_recent"].fillna(False).astype(bool)

    base = pd.DataFrame(index=feat_5m.index).copy()
    base["open"] = feat_5m["open"]
    base["high"] = feat_5m["high"]
    base["low"] = feat_5m["low"]
    base["close"] = feat_5m["close"]
    base["m5_rsi"] = feat_5m["rsi"]
    base["m5_macd"] = feat_5m["macd"]
    base["m5_macd_sig"] = feat_5m["macd_sig"]
    base["m5_atr"] = feat_5m["atr"]

    daily = raw_5m.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    daily["pivot"] = (daily["high"].shift(1) + daily["low"].shift(1) + daily["close"].shift(1)) / 3.0
    base["session"] = base.index.floor("D")
    base["pivot"] = base["session"].map(daily["pivot"].dropna())
    base = base.drop(columns=["session"])

    feat_15m_cols = ["rsi", "macd", "macd_sig"]
    feat_1h_cols = ["rsi", "macd", "macd_sig"]
    if cfg.use_ict_proxy:
        feat_15m_cols += ["ict_bullish_recent", "ict_bearish_recent"]

    tmp_15m = feat_15m[feat_15m_cols].rename(
        columns={
            "rsi": "m15_rsi",
            "macd": "m15_macd",
            "macd_sig": "m15_macd_sig",
            "ict_bullish_recent": "m15_ict_bullish_recent",
            "ict_bearish_recent": "m15_ict_bearish_recent",
        }
    )
    tmp_1h = feat_1h[feat_1h_cols].rename(columns={"rsi": "h1_rsi", "macd": "h1_macd", "macd_sig": "h1_macd_sig"})

    merged = _merge_asof(base, tmp_15m, list(tmp_15m.columns))
    merged = _merge_asof(merged, tmp_1h, list(tmp_1h.columns))

    if cfg.use_ict_proxy:
        merged["m15_ict_bullish_recent"] = merged["m15_ict_bullish_recent"].fillna(False).astype(bool)
        merged["m15_ict_bearish_recent"] = merged["m15_ict_bearish_recent"].fillna(False).astype(bool)

    req = [
        "close",
        "high",
        "low",
        "pivot",
        "m5_rsi",
        "m5_macd",
        "m5_macd_sig",
        "m5_atr",
        "m15_rsi",
        "m15_macd",
        "m15_macd_sig",
        "h1_rsi",
        "h1_macd",
        "h1_macd_sig",
    ]
    merged = merged.dropna(subset=req)
    return merged


def _effective_pivot_offset(row: pd.Series, cfg: FixedRRConfig) -> float:
    pivot = abs(_safe_float(row.get("pivot")))
    return max(abs(cfg.pivot_offset), pivot * max(cfg.pivot_offset_pct, 0.0))


def _long_components(row: pd.Series, prev: pd.Series, cfg: FixedRRConfig) -> dict[str, Any]:
    eff_offset = _effective_pivot_offset(row, cfg)
    near_pivot = row["close"] <= (row["pivot"] - eff_offset) and row["close"] >= (
        row["pivot"] - eff_offset * cfg.pivot_zone_mult
    )
    rsi_ok = row["m15_rsi"] <= cfg.rsi_15m_long_max and row["h1_rsi"] <= cfg.rsi_1h_long_max
    macd_trend = row["m15_macd"] > row["m15_macd_sig"] and row["h1_macd"] > row["h1_macd_sig"]
    macd_cross = row["m5_macd"] > row["m5_macd_sig"] and prev["m5_macd"] <= prev["m5_macd_sig"]
    macd_trigger = macd_cross if cfg.strict_m5_macd_cross else (row["m5_macd"] > row["m5_macd_sig"] or macd_cross)
    ict_ok = bool(row.get("m15_ict_bullish_recent", False)) if cfg.use_ict_proxy else True
    score = int(rsi_ok) + int(macd_trend) + int(macd_trigger)
    enter = bool(near_pivot and score >= cfg.min_confluence and (ict_ok or not cfg.ict_must_confirm))
    return {
        "near_pivot": bool(near_pivot),
        "rsi_ok": bool(rsi_ok),
        "macd_trend": bool(macd_trend),
        "macd_trigger": bool(macd_trigger),
        "ict_ok": bool(ict_ok),
        "score": int(score),
        "enter": enter,
    }


def _short_components(row: pd.Series, prev: pd.Series, cfg: FixedRRConfig) -> dict[str, Any]:
    eff_offset = _effective_pivot_offset(row, cfg)
    near_pivot = row["close"] >= (row["pivot"] + eff_offset) and row["close"] <= (
        row["pivot"] + eff_offset * cfg.pivot_zone_mult
    )
    rsi_ok = row["m15_rsi"] >= cfg.rsi_15m_short_min and row["h1_rsi"] >= cfg.rsi_1h_short_min
    macd_trend = row["m15_macd"] < row["m15_macd_sig"] and row["h1_macd"] < row["h1_macd_sig"]
    macd_cross = row["m5_macd"] < row["m5_macd_sig"] and prev["m5_macd"] >= prev["m5_macd_sig"]
    macd_trigger = macd_cross if cfg.strict_m5_macd_cross else (row["m5_macd"] < row["m5_macd_sig"] or macd_cross)
    ict_ok = bool(row.get("m15_ict_bearish_recent", False)) if cfg.use_ict_proxy else True
    score = int(rsi_ok) + int(macd_trend) + int(macd_trigger)
    enter = bool(near_pivot and score >= cfg.min_confluence and (ict_ok or not cfg.ict_must_confirm))
    return {
        "near_pivot": bool(near_pivot),
        "rsi_ok": bool(rsi_ok),
        "macd_trend": bool(macd_trend),
        "macd_trigger": bool(macd_trigger),
        "ict_ok": bool(ict_ok),
        "score": int(score),
        "enter": enter,
    }


def _long_signal(row: pd.Series, prev: pd.Series, cfg: FixedRRConfig) -> bool:
    return bool(_long_components(row, prev, cfg)["enter"])


def _short_signal(row: pd.Series, prev: pd.Series, cfg: FixedRRConfig) -> bool:
    return bool(_short_components(row, prev, cfg)["enter"])


def _monthly_wr(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=["month", "trades", "wins", "win_rate_pct"])
    tmp = trades_df.copy()
    tmp["month"] = pd.to_datetime(tmp["exit_time"]).dt.to_period("M").astype(str)
    tmp["win"] = (tmp["net_pnl"] > 0).astype(int)
    out = (
        tmp.groupby("month", as_index=False)
        .agg(trades=("net_pnl", "count"), wins=("win", "sum"))
        .sort_values("month")
    )
    out["win_rate_pct"] = np.where(out["trades"] > 0, out["wins"] / out["trades"] * 100.0, 0.0)
    return out


def _run_backtest_on_dataset(cfg: FixedRRConfig, df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or len(df) < 200:
        return {"ok": False, "reason": "No hay suficientes datos para el periodo solicitado"}

    equity = float(cfg.initial_capital)
    fee_rate = cfg.fee_bps / 10_000.0
    position: Optional[dict[str, Any]] = None
    trades: list[dict[str, Any]] = []
    peak = equity
    max_dd = 0.0
    diagnostics = {
        "bars": 0,
        "long_near_pivot": 0,
        "short_near_pivot": 0,
        "long_confluence": 0,
        "short_confluence": 0,
        "long_entries": 0,
        "short_entries": 0,
    }

    for i in range(1, len(df)):
        ts = df.index[i]
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if position is not None:
            position["bars"] += 1
            side = position["side"]
            exit_price = None
            exit_reason = ""

            if side == "LONG":
                sl_hit = row["low"] <= position["sl"]
                tp_hit = row["high"] >= position["tp"]
                if sl_hit and tp_hit:
                    exit_price = position["sl"]
                    exit_reason = "sl_first_same_bar"
                elif sl_hit:
                    exit_price = position["sl"]
                    exit_reason = "stop_loss"
                elif tp_hit:
                    exit_price = position["tp"]
                    exit_reason = "take_profit"
            else:
                sl_hit = row["high"] >= position["sl"]
                tp_hit = row["low"] <= position["tp"]
                if sl_hit and tp_hit:
                    exit_price = position["sl"]
                    exit_reason = "sl_first_same_bar"
                elif sl_hit:
                    exit_price = position["sl"]
                    exit_reason = "stop_loss"
                elif tp_hit:
                    exit_price = position["tp"]
                    exit_reason = "take_profit"

            if exit_price is None and position["bars"] >= cfg.max_hold_bars:
                exit_price = _safe_float(row["close"])
                exit_reason = "time_exit"

            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty if side == "LONG" else (position["entry"] - exit_price) * qty
                fees = fee_rate * ((position["entry"] * qty) + (exit_price * qty))
                net = gross - fees
                equity += net

                trades.append(
                    {
                        "entry_time": position["entry_time"],
                        "exit_time": ts,
                        "side": side,
                        "entry": position["entry"],
                        "exit": exit_price,
                        "qty": qty,
                        "gross_pnl": gross,
                        "fees": fees,
                        "net_pnl": net,
                        "risk_amount": position["risk_amount"],
                        "rr_target": cfg.rr_ratio,
                        "rr_realized": net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0,
                        "exit_reason": exit_reason,
                    }
                )
                position = None

        if position is None:
            diagnostics["bars"] += 1
            long_meta = _long_components(row, prev, cfg)
            short_meta = _short_components(row, prev, cfg)

            diagnostics["long_near_pivot"] += int(long_meta["near_pivot"])
            diagnostics["short_near_pivot"] += int(short_meta["near_pivot"])
            diagnostics["long_confluence"] += int(long_meta["score"] >= cfg.min_confluence)
            diagnostics["short_confluence"] += int(short_meta["score"] >= cfg.min_confluence)

            side = None
            if long_meta["enter"]:
                side = "LONG"
                diagnostics["long_entries"] += 1
            elif short_meta["enter"]:
                side = "SHORT"
                diagnostics["short_entries"] += 1

            if side is not None:
                entry = _safe_float(row["close"])
                atr = max(_safe_float(row["m5_atr"]), 1e-9)
                stop_distance = (
                    max(cfg.fixed_stop_distance, 0.0)
                    if cfg.fixed_stop_distance > 0
                    else max(atr * cfg.atr_mult, cfg.pivot_offset * 0.5)
                )
                if stop_distance <= 0:
                    side = None

            if side is not None:
                sl, tp = _build_stop_target(entry, stop_distance, side, cfg.rr_ratio)
                if cfg.tp_from_pivot:
                    pivot = _safe_float(row["pivot"])
                    tp = _build_pivot_target(pivot, side, cfg.tp_pivot_distance)

                qty, notional, risk_amount = _position_size(
                    equity=equity,
                    entry=entry,
                    stop_distance=stop_distance,
                    risk_pct=cfg.risk_pct,
                    max_notional_pct=cfg.max_notional_pct,
                )
                tp_valid = (tp > entry) if side == "LONG" else (tp < entry)
                if qty > 0 and notional > 0 and risk_amount > 0 and sl > 0 and tp > 0 and tp_valid:
                    position = {
                        "side": side,
                        "entry_time": ts,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "qty": qty,
                        "risk_amount": risk_amount,
                        "bars": 0,
                    }

        unreal = 0.0
        if position is not None:
            if position["side"] == "LONG":
                unreal = (_safe_float(row["close"]) - position["entry"]) * position["qty"]
            else:
                unreal = (position["entry"] - _safe_float(row["close"])) * position["qty"]
        mtm_equity = equity + unreal
        peak = max(peak, mtm_equity)
        dd = (peak - mtm_equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    if position is not None:
        last_ts = df.index[-1]
        last_close = _safe_float(df.iloc[-1]["close"])
        qty = position["qty"]
        gross = (last_close - position["entry"]) * qty if position["side"] == "LONG" else (position["entry"] - last_close) * qty
        fees = fee_rate * ((position["entry"] * qty) + (last_close * qty))
        net = gross - fees
        equity += net
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
                "risk_amount": position["risk_amount"],
                "rr_target": cfg.rr_ratio,
                "rr_realized": net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0,
                "exit_reason": "end_of_backtest",
            }
        )

    trades_df = pd.DataFrame(trades)
    monthly = _monthly_wr(trades_df)
    monthly_last_50 = monthly.tail(50)

    if trades_df.empty:
        return {
            "ok": True,
            "symbol": cfg.symbol,
            "months": cfg.months,
            "rr_fixed": cfg.rr_ratio,
            "risk_pct": cfg.risk_pct,
            "initial_capital": cfg.initial_capital,
            "final_capital": cfg.initial_capital,
            "pnl_quote": 0.0,
            "return_pct": 0.0,
            "trades": 0,
            "win_rate_pct": 0.0,
            "wr_last_50m_pct": 0.0,
            "expectancy_quote": 0.0,
            "profit_factor": 0.0,
            "avg_rr_realized": 0.0,
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "fees_total": 0.0,
            "exit_reasons": {},
            "monthly_wr": monthly_last_50.to_dict(orient="records"),
            "sample_trades": [],
            "diagnostics": diagnostics,
            "reason": "Sin operaciones con filtros actuales",
        }

    wins = trades_df[trades_df["net_pnl"] > 0]
    losses = trades_df[trades_df["net_pnl"] <= 0]
    gross_profit = _safe_float(wins["net_pnl"].sum())
    gross_loss = abs(_safe_float(losses["net_pnl"].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = _safe_float(trades_df["net_pnl"].mean())
    wr = (len(wins) / len(trades_df)) * 100.0 if len(trades_df) else 0.0

    trades_df["month"] = pd.to_datetime(trades_df["exit_time"]).dt.to_period("M").astype(str)
    months_filter = set(monthly_last_50["month"].tolist())
    last_50_df = trades_df[trades_df["month"].isin(months_filter)]
    wr_50m = ((last_50_df["net_pnl"] > 0).sum() / len(last_50_df) * 100.0) if len(last_50_df) else 0.0

    return_pct = 0.0
    if cfg.initial_capital > 0:
        return_pct = ((equity / cfg.initial_capital) - 1.0) * 100.0

    return {
        "ok": True,
        "symbol": cfg.symbol,
        "months": cfg.months,
        "rr_fixed": cfg.rr_ratio,
        "risk_pct": cfg.risk_pct,
        "initial_capital": cfg.initial_capital,
        "final_capital": round(equity, 2),
        "pnl_quote": round(equity - cfg.initial_capital, 2),
        "return_pct": round(return_pct, 2),
        "trades": int(len(trades_df)),
        "win_rate_pct": round(wr, 2),
        "wr_last_50m_pct": round(wr_50m, 2),
        "expectancy_quote": round(expectancy, 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else float("inf"),
        "avg_rr_realized": round(_safe_float(trades_df["rr_realized"].mean()), 4),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "fees_total": round(_safe_float(trades_df["fees"].sum()), 4),
        "exit_reasons": trades_df["exit_reason"].value_counts().to_dict(),
        "monthly_wr": monthly_last_50.to_dict(orient="records"),
        "sample_trades": trades_df.tail(10).to_dict(orient="records"),
        "diagnostics": diagnostics,
    }


def run_backtest(cfg: FixedRRConfig, *, end_dt: Optional[datetime] = None) -> dict[str, Any]:
    end_dt = end_dt or datetime.now(timezone.utc)
    df = _prepare_mtf_dataset(cfg, end_dt=end_dt)
    return _run_backtest_on_dataset(cfg, df)


def _parse_csv_float_list(raw: str) -> list[float]:
    vals: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        vals.append(float(part))
    return vals


def _parse_csv_int_list(raw: str) -> list[int]:
    vals: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        vals.append(int(part))
    return vals


def optimize_profitability(
    base_cfg: FixedRRConfig,
    *,
    end_dt: Optional[datetime] = None,
    pivot_offsets: list[float],
    tp_pivot_distances: list[float],
    rr_values: list[float],
    stop_distances: list[float],
    min_confluences: list[int],
    min_pf: float = 1.0,
    min_return_pct: float = 0.0,
    min_trades: int = 50,
    top_k: int = 10,
) -> dict[str, Any]:
    end_dt = end_dt or datetime.now(timezone.utc)
    prepared = _prepare_mtf_dataset(base_cfg, end_dt=end_dt)
    if prepared.empty or len(prepared) < 200:
        return {
            "ok": False,
            "reason": "No hay suficientes datos para optimización",
            "tested": 0,
            "qualified": 0,
            "top": [],
        }

    tested = 0
    qualified_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    combos = list(product(pivot_offsets, tp_pivot_distances, rr_values, stop_distances, min_confluences))
    total = len(combos)
    print(
        f"[opt] Combinaciones a probar: {total} | filtros: PF>={min_pf}, retorno>={min_return_pct}%, trades>={min_trades}",
        flush=True,
    )

    for idx, (pivot_offset, tp_dist, rr, stop_dist, min_conf) in enumerate(combos, start=1):
        cfg = replace(
            base_cfg,
            pivot_offset=float(pivot_offset),
            pivot_offset_pct=0.0,
            tp_from_pivot=True,
            tp_pivot_distance=float(tp_dist),
            rr_ratio=float(rr),
            fixed_stop_distance=float(stop_dist),
            min_confluence=int(min_conf),
        )

        result = _run_backtest_on_dataset(cfg, prepared)
        tested += 1
        if not result.get("ok", False):
            continue

        row = {
            "pivot_offset": float(pivot_offset),
            "tp_pivot_distance": float(tp_dist),
            "rr": float(rr),
            "fixed_stop_distance": float(stop_dist),
            "min_confluence": int(min_conf),
            "trades": int(result.get("trades", 0)),
            "win_rate_pct": float(result.get("win_rate_pct", 0.0)),
            "return_pct": float(result.get("return_pct", 0.0)),
            "profit_factor": float(result.get("profit_factor", 0.0)),
            "max_drawdown_pct": float(result.get("max_drawdown_pct", 0.0)),
            "expectancy_quote": float(result.get("expectancy_quote", 0.0)),
            "final_capital": float(result.get("final_capital", 0.0)),
            "fees_total": float(result.get("fees_total", 0.0)),
        }
        all_rows.append(row)

        if (
            row["profit_factor"] >= min_pf
            and row["return_pct"] >= min_return_pct
            and row["trades"] >= min_trades
        ):
            qualified_rows.append(row)

        if idx == 1 or idx % 20 == 0 or idx == total:
            print(
                f"[opt] {idx}/{total} | off={pivot_offset:.0f} tpd={tp_dist:.0f} rr={rr:.2f} "
                f"stop={stop_dist:.0f} conf={min_conf} -> ret={row['return_pct']:+.2f}% pf={row['profit_factor']:.3f} "
                f"wr={row['win_rate_pct']:.2f}% trades={row['trades']}",
                flush=True,
            )

    rank_key = lambda r: (r["return_pct"], r["profit_factor"], r["expectancy_quote"], -r["max_drawdown_pct"])
    qualified_rows = sorted(qualified_rows, key=rank_key, reverse=True)
    top_rows = qualified_rows[: max(top_k, 1)]

    best_overall = None
    if all_rows:
        best_overall = sorted(all_rows, key=rank_key, reverse=True)[0]

    return {
        "ok": True,
        "tested": tested,
        "qualified": len(qualified_rows),
        "top": top_rows,
        "best_overall": best_overall,
        "all_rows": all_rows,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backtest BTC con RR fijo (datos Binance).")
    p.add_argument("--symbol", default="BTCUSDC")
    p.add_argument("--months", type=int, default=50, help="Periodo en meses (ejemplo: 50).")
    p.add_argument("--initial-capital", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--tp-from-pivot", action="store_true", help="Usa TP definido desde el pivote en vez de RR.")
    p.add_argument(
        "--tp-pivot-distance",
        type=float,
        default=0.0,
        help="Distancia en dólares entre TP y pivote (si --tp-from-pivot).",
    )
    p.add_argument("--pivot-offset", type=float, default=20.0)
    p.add_argument("--pivot-offset-pct", type=float, default=0.0015)
    p.add_argument("--pivot-zone-mult", type=float, default=8.0)
    p.add_argument("--atr-mult", type=float, default=1.2)
    p.add_argument(
        "--fixed-stop-distance",
        type=float,
        default=0.0,
        help="Distancia fija de stop en dólares. Si > 0, sustituye ATR.",
    )
    p.add_argument("--min-confluence", type=int, default=2, help="Confluencias mínimas de 3 (RSI/MACD trend/MACD trigger).")
    p.add_argument("--ict-must-confirm", action="store_true", help="Si se activa, exige confirmación ICT para entrar.")
    p.add_argument("--strict-m5-macd-cross", action="store_true", help="Si se activa, exige cruce MACD estricto en 5m.")
    p.add_argument("--rsi-15m-long-max", type=float, default=55.0)
    p.add_argument("--rsi-15m-short-min", type=float, default=45.0)
    p.add_argument("--rsi-1h-long-max", type=float, default=60.0)
    p.add_argument("--rsi-1h-short-min", type=float, default=40.0)
    p.add_argument("--max-notional-pct", type=float, default=0.25)
    p.add_argument("--fee-bps", type=float, default=4.0)
    p.add_argument("--max-hold-bars", type=int, default=48)
    p.add_argument("--optimize", action="store_true", help="Ejecuta optimización para buscar PF>1 y retorno positivo.")
    p.add_argument(
        "--opt-pivot-offsets",
        type=str,
        default="150,200,250,300,350,400,500,600",
        help="Lista CSV de offsets al pivote para optimizar.",
    )
    p.add_argument(
        "--opt-tp-pivot-distances",
        type=str,
        default="0,50,100,150,200,300,400,500",
        help="Lista CSV de distancias TP respecto al pivote.",
    )
    p.add_argument(
        "--opt-rr-values",
        type=str,
        default="0.8,1.0,1.2,1.5,2.0",
        help="Lista CSV de RR a evaluar.",
    )
    p.add_argument(
        "--opt-stop-distances",
        type=str,
        default="150,200,250,300,350,500,700",
        help="Lista CSV de stops fijos en dólares.",
    )
    p.add_argument(
        "--opt-min-confluences",
        type=str,
        default="1,2",
        help="Lista CSV de mínimos de confluencia (1..3).",
    )
    p.add_argument("--opt-min-pf", type=float, default=1.0, help="PF mínimo para considerar estrategia válida.")
    p.add_argument(
        "--opt-min-return-pct",
        type=float,
        default=0.0,
        help="Retorno mínimo (%) para considerar estrategia válida.",
    )
    p.add_argument("--opt-min-trades", type=int, default=50, help="Número mínimo de trades para validar estrategia.")
    p.add_argument("--opt-top-k", type=int, default=10, help="Número de estrategias top a mostrar.")
    p.add_argument(
        "--opt-output",
        type=str,
        default=str((BASE_DIR / "logs" / "backtests" / "optimization_pf_positive.csv").as_posix()),
        help="Ruta CSV de salida para resultados de optimización.",
    )
    p.add_argument("--no-ict", action="store_true", help="Desactiva filtros proxy ICT.")
    p.add_argument("--no-cache", action="store_true", help="No usa cache local de klines.")
    return p


def main() -> None:
    args = _parser().parse_args()
    if args.months <= 0:
        print("Error: --months debe ser un entero positivo (meses, no minutos).")
        return
    if args.min_confluence < 1 or args.min_confluence > 3:
        print("Error: --min-confluence debe estar entre 1 y 3.")
        return

    cfg = FixedRRConfig(
        symbol=args.symbol,
        months=args.months,
        initial_capital=args.initial_capital,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr,
        tp_from_pivot=args.tp_from_pivot,
        tp_pivot_distance=args.tp_pivot_distance,
        pivot_offset=args.pivot_offset,
        pivot_offset_pct=args.pivot_offset_pct,
        pivot_zone_mult=args.pivot_zone_mult,
        atr_mult=args.atr_mult,
        fixed_stop_distance=args.fixed_stop_distance,
        min_confluence=args.min_confluence,
        ict_must_confirm=args.ict_must_confirm,
        strict_m5_macd_cross=args.strict_m5_macd_cross,
        rsi_15m_long_max=args.rsi_15m_long_max,
        rsi_15m_short_min=args.rsi_15m_short_min,
        rsi_1h_long_max=args.rsi_1h_long_max,
        rsi_1h_short_min=args.rsi_1h_short_min,
        max_notional_pct=args.max_notional_pct,
        fee_bps=args.fee_bps,
        max_hold_bars=args.max_hold_bars,
        use_ict_proxy=not args.no_ict,
        cache=not args.no_cache,
    )

    if args.optimize:
        try:
            pivot_offsets = _parse_csv_float_list(args.opt_pivot_offsets)
            tp_pivot_distances = _parse_csv_float_list(args.opt_tp_pivot_distances)
            rr_values = _parse_csv_float_list(args.opt_rr_values)
            stop_distances = _parse_csv_float_list(args.opt_stop_distances)
            min_confluences = _parse_csv_int_list(args.opt_min_confluences)
        except ValueError as exc:
            print(f"Error parseando listas de optimización: {exc}")
            return

        if not pivot_offsets or not tp_pivot_distances or not rr_values or not stop_distances or not min_confluences:
            print("Error: todas las listas de optimización deben tener al menos un valor.")
            return
        if any(c < 1 or c > 3 for c in min_confluences):
            print("Error: --opt-min-confluences debe contener solo valores entre 1 y 3.")
            return

        try:
            opt = optimize_profitability(
                cfg,
                pivot_offsets=pivot_offsets,
                tp_pivot_distances=tp_pivot_distances,
                rr_values=rr_values,
                stop_distances=stop_distances,
                min_confluences=min_confluences,
                min_pf=args.opt_min_pf,
                min_return_pct=args.opt_min_return_pct,
                min_trades=args.opt_min_trades,
                top_k=args.opt_top_k,
            )
        except Exception as exc:  # noqa: BLE001
            print("\n" + "=" * 76)
            print("OPTIMIZACIÓN BTC — PF>1 + RETORNO POSITIVO")
            print("=" * 76)
            print(f"Error ejecutando optimización: {exc}")
            print("Revisa conectividad a Binance o ejecuta de nuevo con cache.")
            return

        print("\n" + "=" * 76)
        print("OPTIMIZACIÓN BTC — PF>1 + RETORNO POSITIVO")
        print("=" * 76)
        if not opt.get("ok", False):
            print(f"Error: {opt.get('reason', 'fallo desconocido')}")
            return

        all_rows = opt.get("all_rows", [])
        if all_rows:
            out_df = pd.DataFrame(all_rows).sort_values(
                by=["return_pct", "profit_factor", "expectancy_quote", "max_drawdown_pct"],
                ascending=[False, False, False, True],
            )
            out_path = Path(args.opt_output).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_df.to_csv(out_path, index=False)
            print(f"CSV optimización guardado en: {out_path}")

        print(f"Combinaciones evaluadas: {opt.get('tested', 0)}")
        print(f"Estrategias válidas (PF>={args.opt_min_pf}, retorno>={args.opt_min_return_pct}%): {opt.get('qualified', 0)}")

        top = opt.get("top", [])
        if top:
            print("\nTop estrategias válidas:")
            for i, row in enumerate(top, start=1):
                print(
                    f"{i}. off={row['pivot_offset']:.0f} tpd={row['tp_pivot_distance']:.0f} rr={row['rr']:.2f} "
                    f"stop={row['fixed_stop_distance']:.0f} conf={row['min_confluence']} "
                    f"| ret={row['return_pct']:+.2f}% pf={row['profit_factor']:.4f} wr={row['win_rate_pct']:.2f}% "
                    f"dd={row['max_drawdown_pct']:.2f}% trades={row['trades']}"
                )
        else:
            print("\nNo se encontró ninguna estrategia que cumpla los filtros pedidos.")
            best_overall = opt.get("best_overall")
            if best_overall:
                print(
                    "Mejor combinación sin cumplir filtros: "
                    f"off={best_overall['pivot_offset']:.0f} tpd={best_overall['tp_pivot_distance']:.0f} "
                    f"rr={best_overall['rr']:.2f} stop={best_overall['fixed_stop_distance']:.0f} "
                    f"conf={best_overall['min_confluence']} | ret={best_overall['return_pct']:+.2f}% "
                    f"pf={best_overall['profit_factor']:.4f} wr={best_overall['win_rate_pct']:.2f}% "
                    f"dd={best_overall['max_drawdown_pct']:.2f}% trades={best_overall['trades']}"
                )
        return

    try:
        result = run_backtest(cfg)
    except Exception as exc:  # noqa: BLE001
        print("\n" + "=" * 76)
        print("BACKTEST BTC — RR FIJO + RIESGO FIJO + CONFLUENCIA MTF")
        print("=" * 76)
        print(f"Error ejecutando backtest: {exc}")
        print("Revisa conectividad a Binance o ejecuta de nuevo con cache.")
        return
    print("\n" + "=" * 76)
    print("BACKTEST BTC — RR FIJO + RIESGO FIJO + CONFLUENCIA MTF")
    print("=" * 76)

    if not result.get("ok"):
        print(f"Error: {result.get('reason', 'fallo desconocido')}")
        return

    print(f"Símbolo: {result['symbol']}")
    print(f"Periodo: últimos {result['months']} meses")
    print(f"RR fijo: 1:{result['rr_fixed']}")
    if cfg.tp_from_pivot:
        print(f"TP desde pivote: {cfg.tp_pivot_distance:+.2f}$")
    print(f"Riesgo por trade: {result['risk_pct'] * 100:.2f}%")
    print(f"Capital inicial: ${result.get('initial_capital', 0.0):,.2f}")
    print(f"Capital final:   ${result.get('final_capital', 0.0):,.2f}")
    print(f"PnL:             ${result.get('pnl_quote', 0.0):+,.2f} ({result.get('return_pct', 0.0):+.2f}%)")
    print(f"Trades:          {result['trades']}")
    print(f"Win rate total:  {result['win_rate_pct']:.2f}%")
    print(f"WR últimos 50 meses: {result.get('wr_last_50m_pct', 0.0):.2f}%")
    print(f"Expectancy:      ${result.get('expectancy_quote', 0.0):+.4f}")
    print(f"Profit factor:   {result.get('profit_factor', 0.0)}")
    print(f"Avg RR realized: {result.get('avg_rr_realized', 0.0):+.4f}")
    print(f"Max drawdown:    {result.get('max_drawdown_pct', 0.0):.2f}%")
    print(f"Fees totales:    ${result.get('fees_total', 0.0):,.4f}")
    print(f"Exit reasons:    {result.get('exit_reasons', {})}")
    diagnostics = result.get("diagnostics", {})
    if diagnostics:
        print(f"Diagnóstico barras: {diagnostics}")

    print("\nWR mensual (últimos 50 meses):")
    for row in result.get("monthly_wr", []):
        print(f"- {row['month']}: trades={int(row['trades'])}, wins={int(row['wins'])}, wr={row['win_rate_pct']:.2f}%")


if __name__ == "__main__":
    main()
