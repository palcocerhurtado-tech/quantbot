import time
import schedule
from datetime import datetime

from rich.console import Console
from rich.table import Table

from data.market import fetch_klines, fetch_top_usdt_pairs, get_latest_price, _is_circuit_open
from execution.executor import PaperTrader
from config.settings import SYMBOLS, TIMEFRAMES, KLINES_LIMIT
from logs.logger import get_logger

log     = get_logger("main")
console = Console()
trader  = PaperTrader()

# Dynamic symbol universe — updated each cycle from 24hr ticker
_universe: list[str] = list(SYMBOLS)


def _update_universe() -> None:
    global _universe
    console.print("Actualizando universo de pares Binance...")
    try:
        top = fetch_top_usdt_pairs(min_volume_usdt=5_000_000, top_n=20)
        if top:
            _universe = top
            log.info(f"Universo actualizado: {len(_universe)} pares")
    except Exception as e:
        log.error(f"Error actualizando universo: {e}")


def _build_signal(df) -> dict:
    """Placeholder signal using simple momentum logic until Elliott model is wired in."""
    if df.empty or len(df) < 20:
        return {"signal": "HOLD", "confidence": 0.0, "sl": 0.0, "tp": 0.0}

    close  = df["close"].iloc[-1]
    ma20   = df["close"].rolling(20).mean().iloc[-1]
    ma50   = df["close"].rolling(50).mean().iloc[-1] if len(df) >= 50 else ma20

    if close > ma20 > ma50:
        signal, conf = "BUY",  0.62
    elif close < ma20 < ma50:
        signal, conf = "SELL", 0.62
    else:
        signal, conf = "HOLD", 0.0

    sl = round(close * 0.98, 6) if signal == "BUY"  else round(close * 1.02, 6)
    tp = round(close * 1.03, 6) if signal == "BUY"  else round(close * 0.97, 6)
    return {"signal": signal, "confidence": conf, "sl": sl, "tp": tp}


def run_cycle(timeframe: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    console.rule(f"Ciclo {timeframe.upper()}  {now}")

    # Bail early if we already know the network is down
    if _is_circuit_open():
        log.warning(f"Ciclo {timeframe} omitido — circuit breaker activo")
        return

    _update_universe()

    table = Table(title=f"Elliott {timeframe.upper()} — {now}")
    table.add_column("Par",     style="cyan",   no_wrap=True)
    table.add_column("Precio",  style="white",  no_wrap=True)
    table.add_column("Señal",   style="bold",   no_wrap=True)
    table.add_column("SL",      style="white",  no_wrap=True)
    table.add_column("TP",      style="white",  no_wrap=True)
    table.add_column("Estado",  style="white",  no_wrap=True)

    errors: list[str] = []

    for symbol in _universe:
        # Stop processing remaining symbols if circuit opens mid-cycle
        if _is_circuit_open():
            log.warning(f"Circuit breaker abierto — omitiendo resto de {timeframe}")
            break

        try:
            df = fetch_klines(symbol, timeframe, KLINES_LIMIT)
            if df.empty:
                log.error(f"Ciclo {timeframe} {symbol}: sin datos")
                continue

            price  = get_latest_price(symbol) or df["close"].iloc[-1]
            signal = _build_signal(df)
            result = trader.execute_signal(symbol, signal)

            sig_style = (
                "green" if signal["signal"] == "BUY"
                else "red" if signal["signal"] == "SELL"
                else "yellow"
            )
            estado = "✓ ejecutado" if result.get("executed") else result.get("reason", "—")

            table.add_row(
                symbol,
                f"{price:.4f}",
                f"[{sig_style}]{signal['signal']}[/{sig_style}]",
                f"{signal['sl']:.4f}",
                f"{signal['tp']:.4f}",
                estado,
            )

        except Exception as e:
            log.error(f"Ciclo {timeframe} {symbol}: {e}")
            errors.append(f"{symbol}: {str(e)[:50]}")

    console.print(table)

    portfolio = trader.get_portfolio()
    status    = portfolio["status"]
    console.print(
        f"[bold]Capital:[/bold] ${status['capital']:,.2f} | "
        f"PnL ${status['pnl_total']:+,.2f} | "
        f"Drawdown {status['drawdown']:.1%} | "
        f"Trades {portfolio['n_trades']}"
    )

    if errors:
        console.print(
            f"[red]Errores:[/red] " + " | ".join(errors[:3])
        )


def main() -> None:
    console.print("[bold cyan]" + "=" * 52 + "[/bold cyan]")
    console.print("[bold cyan]  QUANTBOT — BINANCE MULTI-TIMEFRAME BOT[/bold cyan]")
    console.print("[bold cyan]" + "=" * 52 + "[/bold cyan]")
    console.print("[yellow]Modo: PAPER TRADING (sin dinero real)[/yellow]\n")

    # Run initial cycles immediately
    for tf in TIMEFRAMES:
        run_cycle(tf)

    # Schedule recurring cycles
    schedule.every(15).minutes.do(run_cycle, "15m")
    schedule.every(30).minutes.do(run_cycle, "30m")
    schedule.every(1).hours.do(run_cycle, "1h")

    console.print("\n[bold green]Bot activo.[/bold green]")
    console.print("[dim]Pulsa Ctrl+C para detener[/dim]\n")

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
