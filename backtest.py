
import pandas as pd
import numpy as np
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from logs.logger import get_logger

log = get_logger("backtest")

def run_backtest(symbol: str, initial_capital: float = 10000.0) -> dict:
    print(f"\nBacktest {symbol} — cargando 2 años de datos...")
    df   = fetch_ohlcv(symbol, interval="1d", period="730d")
    sent = get_news_sentiment(symbol)
    df   = add_technical_features(df)
    df   = add_sentiment_features(df, sent)

    if len(df) < 60:
        print("Datos insuficientes")
        return {}

    # Entrenamos con el primer 70%, testeamos con el 30% restante
    split      = int(len(df) * 0.70)
    train_df   = df.iloc[:split]
    test_df    = df.iloc[split:]

    predictor  = TradingPredictor(symbol + "_bt")
    predictor.train(train_df)

    capital    = initial_capital
    peak       = initial_capital
    position   = None
    trades     = []
    equity     = [capital]

    for i in range(len(test_df)):
        row     = test_df.iloc[:i+1]
        if len(row) < 5:
            continue

        sig     = predictor.predict(row)
        price   = float(test_df["close"].iloc[i])
        confidence = sig["confidence"]

        # Cerrar posición si la señal cambia
        if position and position["side"] != sig["signal"] and sig["signal"] != "HOLD":
            pnl = (price - position["entry"]) * position["shares"] if position["side"] == "BUY"                   else (position["entry"] - price) * position["shares"]
            capital += pnl
            trades.append({"pnl": pnl, "side": position["side"]})
            position = None
            if capital > peak:
                peak = capital

        # Abrir posición nueva
        if not position and sig["signal"] != "HOLD" and confidence >= 0.55:
            size     = capital * min((confidence - 0.5) * 2 * 0.25, 0.05)
            shares   = size / price
            position = {"side": sig["signal"], "entry": price, "shares": shares}

        equity.append(capital)

    # Cerrar posición final si queda abierta
    if position:
        price = float(test_df["close"].iloc[-1])
        pnl   = (price - position["entry"]) * position["shares"] if position["side"] == "BUY"                 else (position["entry"] - price) * position["shares"]
        capital += pnl
        trades.append({"pnl": pnl, "side": position["side"]})

    # Métricas
    total_trades  = len(trades)
    winners       = [t for t in trades if t["pnl"] > 0]
    losers        = [t for t in trades if t["pnl"] <= 0]
    win_rate      = len(winners) / total_trades if total_trades > 0 else 0
    total_pnl     = capital - initial_capital
    total_return  = total_pnl / initial_capital * 100
    max_dd        = (peak - min(equity)) / peak * 100 if peak > 0 else 0
    avg_win       = np.mean([t["pnl"] for t in winners]) if winners else 0
    avg_loss      = np.mean([t["pnl"] for t in losers])  if losers  else 0

    result = {
        "symbol":        symbol,
        "capital_final": round(capital, 2),
        "pnl":           round(total_pnl, 2),
        "return_pct":    round(total_return, 2),
        "trades":        total_trades,
        "win_rate":      round(win_rate * 100, 1),
        "max_drawdown":  round(max_dd, 1),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
    }
    return result

if __name__ == "__main__":
    symbols = ["AAPL", "MSFT", "NVDA", "SPY"]
    results = []

    print("=" * 55)
    print("BACKTESTING — RESULTADOS HISTÓRICOS")
    print("=" * 55)

    for sym in symbols:
        r = run_backtest(sym)
        if r:
            results.append(r)
            status = "RENTABLE" if r["pnl"] > 0 else "PÉRDIDAS"
            print(f"""
{sym} [{status}]
  Capital final : ${r["capital_final"]:,.2f}  (empezó en $10,000)
  PnL total     : ${r["pnl"]:+,.2f}  ({r["return_pct"]:+.1f}%)
  Trades        : {r["trades"]}
  Win rate      : {r["win_rate"]}%
  Max drawdown  : {r["max_drawdown"]}%
  Ganancia media: ${r["avg_win"]:.2f}
  Pérdida media : ${r["avg_loss"]:.2f}""")

    print("\n" + "=" * 55)
    rentables = [r for r in results if r["pnl"] > 0]
    print(f"Activos rentables: {len(rentables)}/{len(results)}")
    print("=" * 55)
