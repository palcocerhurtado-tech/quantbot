
import time
import schedule
from datetime import datetime
from rich.console import Console
from rich.table import Table

from data.market import fetch_ohlcv, get_latest_price
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from models.elliott_strategy import ElliottStrategy
from execution.executor import PaperTrader
from config.settings import SYMBOLS
from logs.logger import get_logger

log     = get_logger("main")
console = Console()
trader  = PaperTrader()
models  = {}

# ── Símbolos ────────────────────────────────────────────────────────────────
# Acciones: señal vía modelo ML (XGBoost)
ML_SYMBOLS = ["AAPL", "MSFT", "NVDA", "SPY"]

# Crypto: señal vía Elliott Wave Proxy (backtest 50m PF 3.13, WR 63 %)
ELLIOTT_SYMBOLS = ["BTC-USD"]

# Días de histórico diario para Elliott (necesita al menos 220 barras para EMA 200)
ELLIOTT_LOOKBACK_DAYS = 350

elliott = ElliottStrategy()


# ── Entrenamiento de modelos ML ─────────────────────────────────────────────

def train_all_models():
    """Entrena un modelo XGBoost para cada símbolo del catálogo ML."""
    console.print("\n[bold cyan]Entrenando modelos ML...[/bold cyan]")
    for sym in ML_SYMBOLS:
        try:
            df   = fetch_ohlcv(sym, interval="1d", period="180d")
            sent = get_news_sentiment(sym)
            df   = add_technical_features(df)
            df   = add_sentiment_features(df, sent)
            p    = TradingPredictor(sym)
            stats = p.train(df)
            models[sym] = {"predictor": p}
            console.print(f"  [green]✓[/green] {sym} — accuracy {stats.get('accuracy_cv', 0):.1%}")
        except Exception as e:
            console.print(f"  [red]✗[/red] {sym} — error: {e}")


# ── Ciclo principal ─────────────────────────────────────────────────────────

def run_cycle():
    """Ciclo principal — se ejecuta cada hora."""
    now = datetime.now().strftime("%H:%M:%S")
    console.print(f"\n[bold]── Ciclo de trading  {now} ──[/bold]")

    table = Table(title="Señales")
    table.add_column("Símbolo",   style="cyan")
    table.add_column("Precio",    style="white")
    table.add_column("Estrategia",style="dim")
    table.add_column("Señal",     style="bold")
    table.add_column("Conf.",     style="white")
    table.add_column("SL",        style="red")
    table.add_column("TP",        style="green")
    table.add_column("Estado",    style="white")

    # ── 1. Evaluar SL/TP de posiciones abiertas ─────────────────────────
    all_symbols = ML_SYMBOLS + ELLIOTT_SYMBOLS
    for sym in all_symbols:
        exit_check = trader.check_exits(sym)
        if exit_check.get("closed"):
            et  = exit_check["exit_type"]
            pnl = exit_check["pnl"]
            color = "green" if pnl > 0 else "red"
            console.print(
                f"  [{color}]{'✓ TP' if et == 'TP' else '✗ SL'} {sym}[/{color}]  "
                f"PnL [bold {color}]${pnl:+.2f}[/bold {color}]"
            )

    # ── 2. Señales ML para acciones ──────────────────────────────────────
    for sym in ML_SYMBOLS:
        try:
            df   = fetch_ohlcv(sym, interval="1d", period="180d")
            sent = get_news_sentiment(sym)
            df   = add_technical_features(df)
            df   = add_sentiment_features(df, sent)

            if sym not in models:
                p = TradingPredictor(sym)
                p.train(df)
                models[sym] = {"predictor": p}

            signal = models[sym]["predictor"].predict(df)
            price  = get_latest_price(sym)

            sig_color = "green" if signal["signal"] == "BUY" else \
                        "red"   if signal["signal"] == "SELL" else "yellow"
            result = trader.execute_signal(sym, signal)
            estado = "✓ Abierto" if result.get("executed") else "— Hold"

            table.add_row(
                sym, f"${price:.2f}", "ML/XGBoost",
                f"[{sig_color}]{signal['signal']}[/{sig_color}]",
                f"{signal['confidence']:.0%}",
                "—", "—", estado,
            )
        except Exception as e:
            table.add_row(sym, "—", "ML/XGBoost", "ERROR", "—", "—", "—", str(e)[:30])

    # ── 3. Señales Elliott para BTC ──────────────────────────────────────
    for sym in ELLIOTT_SYMBOLS:
        try:
            import yfinance as yf
            from datetime import timedelta
            end   = datetime.utcnow()
            start = end - timedelta(days=ELLIOTT_LOOKBACK_DAYS)
            raw = yf.Ticker(sym).history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
            )
            if raw.empty:
                raise ValueError("Sin datos de yfinance")
            raw.columns  = [c.lower() for c in raw.columns]
            raw.index    = raw.index.tz_localize(None)

            signal = elliott.get_signal(raw)
            price  = float(raw["close"].iloc[-1])

            if signal["signal"] == "BUY":
                result = trader.execute_signal(sym, signal)
                estado = "✓ Abierto" if result.get("executed") else "— Ya en posición"
                sl_str = f"${signal['sl']:,.0f}"
                tp_str = f"${signal['tp']:,.0f}"
                sig_color = "green"
            else:
                estado    = "— Espera"
                sl_str    = "—"
                tp_str    = "—"
                sig_color = "yellow"

            table.add_row(
                sym, f"${price:,.0f}", "Elliott Proxy",
                f"[{sig_color}]{signal['signal']}[/{sig_color}]",
                f"{signal['confidence']:.0%}",
                sl_str, tp_str, estado,
            )
        except Exception as e:
            table.add_row(sym, "—", "Elliott", "ERROR", "—", "—", "—", str(e)[:30])

    console.print(table)

    # ── 4. Resumen del portfolio ─────────────────────────────────────────
    portfolio = trader.get_portfolio()
    status    = portfolio["status"]
    console.print(
        f"[bold]Portfolio:[/bold] "
        f"Capital [cyan]${status['capital']:,.2f}[/cyan] | "
        f"PnL [bold {'green' if status['pnl_total'] >= 0 else 'red'}]"
        f"${status['pnl_total']:+,.2f}[/bold {'green' if status['pnl_total'] >= 0 else 'red'}] | "
        f"Drawdown {status['drawdown']:.1%} | "
        f"Trades {portfolio['n_trades']}"
    )

    # Detalle de posiciones abiertas con SL/TP
    if portfolio["positions_detail"]:
        console.print("\n[bold]Posiciones abiertas:[/bold]")
        for sym, pos in portfolio["positions_detail"].items():
            sl_info = f"SL ${pos['sl']:,.0f}" if pos["sl"] else "SL —"
            tp_info = f"TP ${pos['tp']:,.0f}" if pos["tp"] else "TP —"
            pnl_color = "green" if pos["unrealized"] >= 0 else "red"
            console.print(
                f"  {sym}: entrada ${pos['entry']:,.2f} | actual ${pos['current']:,.2f} | "
                f"[{pnl_color}]PnL ${pos['unrealized']:+,.2f}[/{pnl_color}] | "
                f"{sl_info}  {tp_info}"
            )


def main():
    console.print("[bold cyan]" + "=" * 52 + "[/bold cyan]")
    console.print("[bold cyan]  QUANTBOT — Elliott BTC + ML Stocks[/bold cyan]")
    console.print("[bold cyan]" + "=" * 52 + "[/bold cyan]")
    console.print("[yellow]Modo: PAPER TRADING (sin dinero real)[/yellow]")
    console.print(
        f"  Estrategia BTC : Elliott Wave Proxy (PF 3.13, WR 63 %)\n"
        f"  Estrategia ML  : XGBoost para {', '.join(ML_SYMBOLS)}\n"
    )

    train_all_models()
    run_cycle()

    schedule.every(1).hours.do(run_cycle)
    console.print("\n[bold green]Bot activo. Próximo ciclo en 1 hora.[/bold green]")
    console.print("[dim]Pulsa Ctrl+C para detener[/dim]\n")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
