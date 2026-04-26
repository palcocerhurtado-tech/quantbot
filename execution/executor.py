"""
execution/executor.py
=====================
PaperTrader  — simula operaciones sin dinero real.
LiveTrader   — ejecuta órdenes reales en Binance Spot via REST API.

Selección automática según TRADING_MODE en .env:
    TRADING_MODE=paper  →  PaperTrader  (por defecto)
    TRADING_MODE=live   →  LiveTrader
"""

import hashlib
import hmac
import math
import time as _time
from datetime import datetime
from urllib.parse import urlencode

import requests as _requests

from config.settings import (
    BINANCE_API_BASE, BINANCE_API_KEY, BINANCE_SECRET_KEY,
    RISK_PER_TRADE, MAX_POSITION_PCT, LIVE_TRADING_ENABLED,
    STOP_ATR_MULT, MIN_STOP_DISTANCE_PCT, BREAKEVEN_TRIGGER_R,
    TRAILING_TRIGGER_R, TRAILING_ATR_MULT, PARTIAL_TAKE_PROFIT_R,
    PARTIAL_TAKE_PROFIT_PCT,
)
from data.market import get_latest_price
from execution.ledger import OrderLedger
from execution.risk import RiskManager
from logs.logger import get_logger, trade_logger

log = get_logger("executor")


# ══════════════════════════════════════════════════════════════════════════════
#  PAPER TRADER
# ══════════════════════════════════════════════════════════════════════════════

class PaperTrader:
    """Simula trades sin tocar dinero real."""

    def __init__(self):
        self.risk      = RiskManager()
        if self.risk.current_capital < 50:
            self.risk.initial_capital = 10_000.0
            self.risk.current_capital = 10_000.0
            self.risk.peak_capital = 10_000.0
        self.trades    = []
        self._sl_tp: dict = {}
        self.exit_plans: dict = {}
        self.ledger = OrderLedger()

    @property
    def open_positions(self) -> dict:
        return self.risk.open_positions

    def _get_market(self, symbol: str) -> dict:
        price = get_latest_price(symbol)
        return {"price": price, "spread_pct": 0.0, "slippage_bps": 0.0}

    def _client_order_id(self, symbol: str, label: str) -> str:
        return f"qb-{label.lower()}-{symbol.lower()}-{int(_time.time() * 1000)}"

    def _reject(self, symbol: str, signal: dict, reason: str, *, side: str = "BUY", context=None) -> dict:
        client_order_id = self._client_order_id(symbol, signal.get("signal", side))
        self.ledger.record_rejection(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            reason=reason,
            signal=signal,
            mode="paper",
            metadata=context or {},
        )
        log.info(f"Trade rechazado {symbol} {signal.get('signal')}: {reason} clientOrderId={client_order_id}")
        return {"executed": False, "reason": reason, "client_order_id": client_order_id}

    def execute_signal(self, symbol: str, signal: dict) -> dict:
        price = float(signal.get("price") or 0.0)
        if price <= 0:
            try:
                price = float(self._get_market(symbol).get("price") or 0.0)
            except Exception:  # noqa: BLE001 - tests use this to skip market validation
                price = float(signal.get("price") or 0.0)

        atr = float(signal.get("atr") or 0.0)
        stop_distance = 0.0
        size = 0.0
        shares = 0.0
        risk_budget = self.risk.current_capital * RISK_PER_TRADE
        if price > 0 and atr > 0:
            stop_distance = max(atr * STOP_ATR_MULT, price * MIN_STOP_DISTANCE_PCT)
            shares = risk_budget / stop_distance if stop_distance > 0 else 0.0
            size = shares * price
        context = {
            "price": price,
            "requested_qty": shares,
            "notional": size,
        }
        if not signal.get("price"):
            try:
                market = self._get_market(symbol)
                context["spread_pct"] = float(market.get("spread_pct") or 0.0)
                context["slippage_bps"] = float(market.get("slippage_bps") or 0.0)
            except Exception:  # noqa: BLE001
                pass

        can_trade, reason = self.risk.can_trade(symbol, signal, trader=self, context=context)
        if not can_trade:
            return self._reject(symbol, signal, reason, context=context)

        if price <= 0:
            return self._reject(symbol, signal, "Precio no disponible", context=context)

        if size <= 0 and signal.get("sl") and signal.get("entry") and float(signal["sl"]) > 0:
            size = self.risk.fixed_risk_position_size(float(signal["entry"]), float(signal["sl"]))
        elif size <= 0:
            size = self.risk.kelly_position_size(signal["confidence"], self.risk.current_capital)
        if size <= 0:
            return self._reject(symbol, signal, "Size = 0", context=context)

        shares = size / price
        stop_distance = stop_distance or abs(float(signal.get("entry", price)) - float(signal.get("sl", price)))
        stop_price = float(signal.get("sl") or (price - stop_distance))
        tp_price = float(signal.get("tp") or (price + stop_distance * 2.0))
        client_order_id = self._client_order_id(symbol, "buy")
        trade = {
            "symbol": symbol, "action": signal["signal"],
            "price": round(price, 6), "entry_price": round(price, 6), "size_usd": size,
            "shares": round(shares, 8), "confidence": signal["confidence"],
            "timestamp": datetime.utcnow().isoformat(),
            "capital_before": round(self.risk.current_capital, 2),
            "sl": stop_price, "tp": tp_price,
            "stop_distance": stop_distance, "risk_budget": risk_budget,
            "client_order_id": client_order_id,
        }
        self.risk.open_positions[symbol] = trade
        self._sl_tp[symbol] = {"sl": stop_price, "tp": tp_price, "shares": shares}
        self.exit_plans[symbol] = {
            "entry_price": price,
            "stop_price": stop_price,
            "take_profit_price": tp_price,
            "stop_distance": stop_distance,
            "risk_budget": risk_budget,
            "partial_taken": False,
            "highest_price": price,
        }
        trade_logger.log_trade(trade)
        self.trades.append(trade)
        log.info(f"[PAPER] BUY {symbol} @ ${price:.4f} | ${size:.2f}")
        return {"executed": True, "trade": trade}

    def manage_position(self, symbol: str, market: dict) -> dict:
        if symbol not in self.risk.open_positions or symbol not in self.exit_plans:
            return {"executed": False, "reason": "Sin posición"}

        price = float(market.get("price") or 0.0)
        atr = float(market.get("atr") or 0.0)
        plan = self.exit_plans[symbol]
        position = self.risk.open_positions[symbol]
        entry = float(plan["entry_price"])
        stop_distance = float(plan["stop_distance"])
        shares = float(position["shares"])
        if price <= 0 or stop_distance <= 0 or shares <= 0:
            return {"executed": False, "reason": "Datos de mercado inválidos"}

        plan["highest_price"] = max(float(plan.get("highest_price", entry)), price)
        r_multiple = (price - entry) / stop_distance

        if not plan.get("partial_taken") and r_multiple >= PARTIAL_TAKE_PROFIT_R:
            sell_shares = shares * PARTIAL_TAKE_PROFIT_PCT
            remaining = shares - sell_shares
            pnl = (price - entry) * sell_shares
            position["shares"] = remaining
            plan["partial_taken"] = True
            if r_multiple >= BREAKEVEN_TRIGGER_R:
                plan["stop_price"] = max(float(plan["stop_price"]), entry)
            self.trades.append({"type": "CLOSE", "symbol": symbol, "pnl": pnl, "signal_label": "TP"})
            return {"executed": True, "signal_label": "TP", "shares": sell_shares, "pnl": round(pnl, 2)}

        if r_multiple >= TRAILING_TRIGGER_R and atr > 0:
            trailing_stop = price - (atr * TRAILING_ATR_MULT)
            plan["stop_price"] = max(float(plan["stop_price"]), trailing_stop, entry)

        if price <= float(plan["stop_price"]):
            pnl = (price - entry) * shares
            self.risk.update_capital(pnl)
            del self.risk.open_positions[symbol]
            del self.exit_plans[symbol]
            self._sl_tp.pop(symbol, None)
            self.trades.append({"type": "CLOSE", "symbol": symbol, "pnl": pnl, "signal_label": "STOP"})
            return {"executed": True, "signal_label": "STOP", "pnl": round(pnl, 2)}

        return {"executed": False, "signal_label": "HOLD"}

    def check_exits(self, symbol: str) -> dict:
        if symbol not in self._sl_tp or symbol not in self.risk.open_positions:
            return {"checked": False}
        levels = self._sl_tp[symbol]
        price  = get_latest_price(symbol)
        if price <= 0:
            return {"checked": True, "closed": False}
        sl, tp = levels["sl"], levels["tp"]
        hit_sl = price <= sl
        hit_tp = price >= tp
        if hit_sl or hit_tp:
            exit_price = sl if hit_sl else tp
            exit_type  = "SL" if hit_sl else "TP"
            entry      = self.risk.open_positions[symbol]["price"]
            pnl        = (exit_price - entry) * levels["shares"]
            self.risk.update_capital(pnl)
            del self.risk.open_positions[symbol]
            del self._sl_tp[symbol]
            result = {
                "checked": True, "closed": True, "symbol": symbol,
                "exit_type": exit_type, "entry_price": entry,
                "exit_price": round(exit_price, 6), "pnl": round(pnl, 2),
                "timestamp": datetime.utcnow().isoformat(),
            }
            trade_logger.log_trade({**result, "type": "CLOSE"})
            log.info(f"[PAPER] {'TP' if exit_type=='TP' else 'SL'} {symbol} PnL=${pnl:.2f}")
            return result
        unrealized = (price - self.risk.open_positions[symbol]["price"]) * levels["shares"]
        return {"checked": True, "closed": False, "current_price": price,
                "sl": sl, "tp": tp, "unrealized_pnl": round(unrealized, 2)}

    def close_position(self, symbol: str) -> dict:
        if symbol not in self.risk.open_positions:
            return {"closed": False, "reason": "Sin posición"}
        entry  = self.risk.open_positions[symbol]
        price  = get_latest_price(symbol)
        pnl    = (price - entry["price"]) * entry["shares"]
        self.risk.update_capital(pnl)
        del self.risk.open_positions[symbol]
        self._sl_tp.pop(symbol, None)
        result = {"symbol": symbol, "entry_price": entry["price"],
                  "exit_price": round(price, 6), "pnl": round(pnl, 2),
                  "timestamp": datetime.utcnow().isoformat()}
        trade_logger.log_trade({**result, "type": "CLOSE_MANUAL"})
        return result

    def get_portfolio(self) -> dict:
        details = {}
        for sym, pos in self.risk.open_positions.items():
            price = get_latest_price(sym)
            unr   = (price - pos["price"]) * pos["shares"] if price > 0 else 0
            details[sym] = {
                "entry": pos["price"], "current": round(price, 6),
                "unrealized": round(unr, 2),
                "sl": self._sl_tp.get(sym, {}).get("sl"),
                "tp": self._sl_tp.get(sym, {}).get("tp"),
            }
        return {"status": self.risk.get_status(), "positions": list(self.risk.open_positions),
                "positions_detail": details, "n_trades": len(self.trades)}

    def get_stats(self) -> dict:
        return _calc_stats(self.trades, self.risk)


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED STATS
# ══════════════════════════════════════════════════════════════════════════════

def _calc_stats(trades: list, risk) -> dict:
    """Calcula métricas de rendimiento a partir del historial de trades."""
    closed = [t for t in trades if t.get("type") in ("CLOSE", "CLOSE_MANUAL")]
    if not closed:
        # Intentar inferir PnL desde el historial de trades abiertos con pnl
        closed = [t for t in trades if "pnl" in t]

    total = len(closed)
    if total == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "return_pct": 0.0,
            "open_trades": len(risk.open_positions),
        }

    pnls        = [float(t["pnl"]) for t in closed]
    winners     = [p for p in pnls if p > 0]
    losers      = [p for p in pnls if p <= 0]
    gross_profit = sum(winners) if winners else 0.0
    gross_loss   = abs(sum(losers)) if losers else 0.0
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate     = len(winners) / total if total > 0 else 0.0
    avg_win      = gross_profit / len(winners) if winners else 0.0
    avg_loss     = gross_loss / len(losers) if losers else 0.0
    expectancy   = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    return_pct   = (risk.current_capital - risk.initial_capital) / risk.initial_capital

    return {
        "total_trades":  total,
        "win_rate":      round(win_rate, 4),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999.0,
        "expectancy":    round(expectancy, 2),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "gross_profit":  round(gross_profit, 2),
        "gross_loss":    round(gross_loss, 2),
        "best_trade":    round(max(pnls), 2),
        "worst_trade":   round(min(pnls), 2),
        "return_pct":    round(return_pct, 4),
        "open_trades":   len(risk.open_positions),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE TRADER
# ══════════════════════════════════════════════════════════════════════════════

class LiveTrader:
    """
    Ejecuta órdenes reales en Binance Spot.
    - Market orders para entrada y salida.
    - SL/TP gestionados internamente (evaluados cada ciclo).
    - Capital sincronizado desde el balance USDT real de Binance.
    """

    def __init__(self):
        if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
            raise RuntimeError(
                "BINANCE_API_KEY y BINANCE_SECRET_KEY deben estar en el .env para modo live."
            )
        self.risk       = RiskManager()
        self.trades     = []
        self._sl_tp: dict   = {}
        self._lot_cache: dict = {}

        # Sincronizar capital desde Binance
        balance = self._get_quote_balance()
        if balance > 0:
            self.risk.initial_capital = balance
            self.risk.current_capital = balance
            self.risk.peak_capital    = balance
            log.info(f"[LIVE] Balance Binance: ${balance:.2f} USDT")
        else:
            log.warning("[LIVE] No se pudo obtener balance — usando capital por defecto")

    # ── Binance REST privado ──────────────────────────────────────────────────

    def _signed(self, method: str, path: str, params: dict = None) -> dict:
        """Realiza una request firmada HMAC-SHA256 a la API privada de Binance."""
        params = dict(params or {})
        params["timestamp"]  = int(_time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig   = hmac.new(
            BINANCE_SECRET_KEY.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        url     = BINANCE_API_BASE + path
        r = getattr(_requests, method.lower())(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def _get_quote_balance(self) -> float:
        from config.settings import QUOTE
        try:
            data = self._signed("GET", "/api/v3/account")
            for asset in data.get("balances", []):
                if asset["asset"] == QUOTE:
                    return float(asset["free"])
        except Exception as e:
            log.error(f"Error obteniendo balance: {e}")
        return 0.0

    def _sync_capital(self):
        bal = self._get_quote_balance()
        if bal > 0:
            self.risk.current_capital = bal
            if bal > self.risk.peak_capital:
                self.risk.peak_capital = bal

    # ── Lot size y redondeo ────────────────────────────────────────────────────

    def _get_lot_size(self, symbol: str) -> dict:
        if symbol in self._lot_cache:
            return self._lot_cache[symbol]
        try:
            data = _requests.get(
                BINANCE_API_BASE + "/api/v3/exchangeInfo",
                params={"symbol": symbol}, timeout=10,
            ).json()
            for filt in data["symbols"][0]["filters"]:
                if filt["filterType"] == "LOT_SIZE":
                    info = {
                        "step": float(filt["stepSize"]),
                        "min":  float(filt["minQty"]),
                    }
                    self._lot_cache[symbol] = info
                    return info
        except Exception as e:
            log.warning(f"lot_size {symbol}: {e}")
        return {"step": 1e-5, "min": 1e-5}

    def _round_qty(self, symbol: str, qty: float) -> float:
        lot  = self._get_lot_size(symbol)
        step = lot["step"]
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        qty = math.floor(qty / step) * step
        qty = round(qty, precision)
        return max(qty, lot["min"])

    # ── Órdenes ────────────────────────────────────────────────────────────────

    def _market_order(self, symbol: str, side: str, qty: float) -> dict:
        qty_r = self._round_qty(symbol, qty)
        log.info(f"[LIVE] Orden MARKET {side} {qty_r} {symbol}")
        return self._signed("POST", "/api/v3/order", {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": qty_r,
        })

    def _fill_price(self, order: dict, fallback: float) -> float:
        fills = order.get("fills", [])
        if fills:
            total_qty  = sum(float(f["qty"])   for f in fills)
            total_cost = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            return total_cost / total_qty if total_qty > 0 else fallback
        return fallback

    # ── Interfaz pública (misma que PaperTrader) ──────────────────────────────

    def execute_signal(self, symbol: str, signal: dict) -> dict:
        can_trade, reason = self.risk.can_trade(symbol, signal)
        if not can_trade:
            return {"executed": False, "reason": reason}

        price = get_latest_price(symbol)
        if price <= 0:
            return {"executed": False, "reason": "Precio no disponible"}

        if signal.get("sl") and signal.get("entry") and float(signal["sl"]) > 0:
            size = self.risk.fixed_risk_position_size(float(signal["entry"]), float(signal["sl"]))
        else:
            size = self.risk.kelly_position_size(signal["confidence"], self.risk.current_capital)
        if size <= 0:
            return {"executed": False, "reason": "Size = 0"}

        qty = size / price
        try:
            order      = self._market_order(symbol, "BUY", qty)
            fill       = self._fill_price(order, price)
            actual_qty = float(order.get("executedQty", qty))

            trade = {
                "symbol": symbol, "action": "BUY",
                "price": round(fill, 6), "size_usd": round(fill * actual_qty, 2),
                "shares": actual_qty, "confidence": signal["confidence"],
                "timestamp": datetime.utcnow().isoformat(),
                "order_id": order.get("orderId"),
                "sl": signal.get("sl"), "tp": signal.get("tp"),
            }
            self.risk.open_positions[symbol] = trade
            if signal.get("sl") and signal.get("tp"):
                self._sl_tp[symbol] = {
                    "sl": signal["sl"], "tp": signal["tp"], "shares": actual_qty,
                }
            self._sync_capital()
            trade_logger.log_trade(trade)
            self.trades.append(trade)
            log.info(f"[LIVE] BUY {symbol} @ ${fill:.4f} qty={actual_qty} order={order.get('orderId')}")
            return {"executed": True, "trade": trade}
        except Exception as e:
            log.error(f"[LIVE] Error BUY {symbol}: {e}")
            return {"executed": False, "reason": str(e)}

    def check_exits(self, symbol: str) -> dict:
        if symbol not in self._sl_tp or symbol not in self.risk.open_positions:
            return {"checked": False}
        levels = self._sl_tp[symbol]
        price  = get_latest_price(symbol)
        if price <= 0:
            return {"checked": True, "closed": False}
        sl, tp   = levels["sl"], levels["tp"]
        hit_sl   = price <= sl
        hit_tp   = price >= tp
        if hit_sl or hit_tp:
            exit_type = "SL" if hit_sl else "TP"
            shares    = levels["shares"]
            try:
                order      = self._market_order(symbol, "SELL", shares)
                fill       = self._fill_price(order, price)
                entry      = self.risk.open_positions[symbol]["price"]
                pnl        = (fill - entry) * float(order.get("executedQty", shares))
                del self.risk.open_positions[symbol]
                del self._sl_tp[symbol]
                self._sync_capital()
                result = {
                    "checked": True, "closed": True, "symbol": symbol,
                    "exit_type": exit_type, "entry_price": entry,
                    "exit_price": round(fill, 6), "pnl": round(pnl, 2),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                trade_logger.log_trade({**result, "type": "CLOSE"})
                log.info(f"[LIVE] {'TP' if exit_type=='TP' else 'SL'} {symbol} PnL=${pnl:.2f}")
                return result
            except Exception as e:
                log.error(f"[LIVE] Error SELL {symbol}: {e}")
                return {"checked": True, "closed": False, "reason": str(e)}
        unrealized = (price - self.risk.open_positions[symbol]["price"]) * levels["shares"]
        return {"checked": True, "closed": False, "current_price": price,
                "sl": sl, "tp": tp, "unrealized_pnl": round(unrealized, 2)}

    def close_position(self, symbol: str) -> dict:
        if symbol not in self.risk.open_positions:
            return {"closed": False, "reason": "Sin posición"}
        pos = self.risk.open_positions[symbol]
        try:
            order = self._market_order(symbol, "SELL", pos["shares"])
            fill  = self._fill_price(order, get_latest_price(symbol))
            pnl   = (fill - pos["price"]) * float(order.get("executedQty", pos["shares"]))
            del self.risk.open_positions[symbol]
            self._sl_tp.pop(symbol, None)
            self._sync_capital()
            result = {"symbol": symbol, "entry_price": pos["price"],
                      "exit_price": round(fill, 6), "pnl": round(pnl, 2),
                      "timestamp": datetime.utcnow().isoformat()}
            trade_logger.log_trade({**result, "type": "CLOSE_MANUAL"})
            return result
        except Exception as e:
            log.error(f"[LIVE] Error close {symbol}: {e}")
            return {"closed": False, "reason": str(e)}

    def get_portfolio(self) -> dict:
        details = {}
        for sym, pos in self.risk.open_positions.items():
            price = get_latest_price(sym)
            unr   = (price - pos["price"]) * pos["shares"] if price > 0 else 0
            details[sym] = {
                "entry": pos["price"], "current": round(price, 6),
                "unrealized": round(unr, 2),
                "sl": self._sl_tp.get(sym, {}).get("sl"),
                "tp": self._sl_tp.get(sym, {}).get("tp"),
            }
        return {"status": self.risk.get_status(), "positions": list(self.risk.open_positions),
                "positions_detail": details, "n_trades": len(self.trades)}

    def get_stats(self) -> dict:
        return _calc_stats(self.trades, self.risk)
