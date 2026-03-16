from pathlib import Path
base = Path.home() / "Desktop" / "quantbot"

# ── backtest.py ───────────────────────────────────────────────────
(base / "backtest.py").write_text('''
import pandas as pd
import numpy as np
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from logs.logger import get_logger

log = get_logger("backtest")

def run_backtest(symbol: str, initial_capital: float = 10000.0) -> dict:
    print(f"\\nBacktest {symbol} — cargando 2 años de datos...")
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
            pnl = (price - position["entry"]) * position["shares"] if position["side"] == "BUY" \
                  else (position["entry"] - price) * position["shares"]
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
        pnl   = (price - position["entry"]) * position["shares"] if position["side"] == "BUY" \
                else (position["entry"] - price) * position["shares"]
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

    print("\\n" + "=" * 55)
    rentables = [r for r in results if r["pnl"] > 0]
    print(f"Activos rentables: {len(rentables)}/{len(results)}")
    print("=" * 55)
''')

# ── main.py ───────────────────────────────────────────────────────
(base / "main.py").write_text('''
import time
import schedule
from datetime import datetime
from rich.console import Console
from rich.table import Table

from data.market import fetch_ohlcv, get_latest_price
from data.news import get_news_sentiment
from data.reddit import get_reddit_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from execution.executor import PaperTrader
from config.settings import SYMBOLS
from logs.logger import get_logger

log     = console = Console()
log     = get_logger("main")
trader  = PaperTrader()
models  = {}

SYMBOLS_ACTIVE = ["AAPL", "MSFT", "NVDA", "SPY"]

def train_all_models():
    """Entrena un modelo para cada símbolo al arrancar."""
    console.print("\\n[bold cyan]Entrenando modelos...[/bold cyan]")
    for sym in SYMBOLS_ACTIVE:
        try:
            df   = fetch_ohlcv(sym, interval="1d", period="180d")
            sent = get_news_sentiment(sym)
            df   = add_technical_features(df)
            df   = add_sentiment_features(df, sent)
            p    = TradingPredictor(sym)
            stats= p.train(df)
            models[sym] = {"predictor": p, "df": df}
            console.print(f"  [green]✓[/green] {sym} — accuracy {stats.get('accuracy_cv', 0):.1%}")
        except Exception as e:
            console.print(f"  [red]✗[/red] {sym} — error: {e}")

def run_cycle():
    """Ciclo principal — se ejecuta cada hora."""
    now = datetime.now().strftime("%H:%M:%S")
    console.print(f"\\n[bold]Ciclo de trading — {now}[/bold]")

    table = Table(title="Señales actuales")
    table.add_column("Símbolo",    style="cyan")
    table.add_column("Precio",     style="white")
    table.add_column("Señal",      style="bold")
    table.add_column("Confianza",  style="white")
    table.add_column("RSI",        style="white")
    table.add_column("Ejecutado",  style="white")

    for sym in SYMBOLS_ACTIVE:
        try:
            df   = fetch_ohlcv(sym, interval="1d", period="180d")
            sent = get_news_sentiment(sym)
            df   = add_technical_features(df)
            df   = add_sentiment_features(df, sent)

            if sym not in models:
                p = TradingPredictor(sym)
                p.train(df)
                models[sym] = {"predictor": p}

            predictor = models[sym]["predictor"]
            signal    = predictor.predict(df)
            price     = get_latest_price(sym)
            rsi       = round(df["rsi"].iloc[-1], 1)

            sig_color = "green" if signal["signal"] == "BUY" else \
                        "red"   if signal["signal"] == "SELL" else "yellow"

            result    = trader.execute_signal(sym, signal)
            executed  = "✓ SÍ" if result["executed"] else "— NO"

            table.add_row(
                sym,
                f"${price:.2f}",
                f"[{sig_color}]{signal['signal']}[/{sig_color}]",
                f"{signal['confidence']:.1%}",
                str(rsi),
                executed
            )
        except Exception as e:
            table.add_row(sym, "—", "ERROR", "—", "—", str(e))

    console.print(table)

    # Estado del portfolio
    portfolio = trader.get_portfolio()
    status    = portfolio["status"]
    console.print(f"[bold]Portfolio:[/bold] "
                  f"Capital ${status['capital']:,.2f} | "
                  f"PnL ${status['pnl_total']:+,.2f} | "
                  f"Drawdown {status['drawdown']:.1%} | "
                  f"Trades {portfolio['n_trades']}")

def main():
    console.print("[bold cyan]" + "=" * 50 + "[/bold cyan]")
    console.print("[bold cyan]  QUANTBOT — SISTEMA DE TRADING ACTIVO[/bold cyan]")
    console.print("[bold cyan]" + "=" * 50 + "[/bold cyan]")
    console.print("[yellow]Modo: PAPER TRADING (sin dinero real)[/yellow]")

    # Entrena modelos al arrancar
    train_all_models()

    # Primer ciclo inmediato
    run_cycle()

    # Programa ciclo cada hora
    schedule.every(1).hours.do(run_cycle)

    console.print("\\n[bold green]Bot activo. Próximo ciclo en 1 hora.[/bold green]")
    console.print("[dim]Pulsa Ctrl+C para detener[/dim]\\n")

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
''')

print("=" * 45)
print("ARCHIVOS CREADOS")
print("=" * 45)
print("  backtest.py  — prueba histórica")
print("  main.py      — bot automático")
