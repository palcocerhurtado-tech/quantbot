
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
    console.print("\n[bold cyan]Entrenando modelos...[/bold cyan]")
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
    console.print(f"\n[bold]Ciclo de trading — {now}[/bold]")

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

            sig_color = "green" if signal["signal"] == "BUY" else                         "red"   if signal["signal"] == "SELL" else "yellow"

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

    console.print("\n[bold green]Bot activo. Próximo ciclo en 1 hora.[/bold green]")
    console.print("[dim]Pulsa Ctrl+C para detener[/dim]\n")

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
