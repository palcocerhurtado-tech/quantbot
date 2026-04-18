"""
main.py — QuantBot Crypto
=========================
Escanea los top-40 pares de Binance por volumen y opera los 10 más líquidos
usando la estrategia Elliott Wave Proxy (PF 3.13, WR 63 % en backtest 50 meses).

Ciclos programados:
  • Cada 15 min → analiza velas de 15m
  • Cada 30 min → analiza velas de 30m
  • Cada 60 min → analiza velas de 1h
  • Cada 90 min → analiza velas de 1h (no existe 90m en Binance)

Cada ciclo:
  1. Refresca el universo (top-40 → top-10 más líquidos) si han pasado > 30 min
  2. Comprueba si alguna posición abierta tocó SL o TP y la cierra
  3. Busca señales Elliott en los símbolos sin posición abierta
  4. Abre posición si pasa el filtro de riesgo
"""

import time
import schedule
from datetime import datetime
from rich.console import Console
from rich.table import Table

from data.market import get_top_pairs_by_volume, fetch_ohlcv, get_latest_price
from models.elliott_strategy import ElliottStrategy
from execution.executor import PaperTrader, LiveTrader
from config.settings import (
    QUOTE, TOP_N_SCAN, TOP_N_TRADE,
    CYCLE_TIMEFRAMES, OHLCV_LIMIT,
    INITIAL_CAPITAL, TRADING_MODE,
)
from logs.logger import get_logger

log     = get_logger("main")
console = Console()
elliott = ElliottStrategy()

if TRADING_MODE == "live":
    trader = LiveTrader()
    console.print("[bold red]⚠  MODO LIVE — ÓRDENES REALES EN BINANCE[/bold red]")
else:
    trader = PaperTrader()

# ── Caché del universo (se refresca cada 30 min) ────────────────────────────
_universe: dict = {"symbols": [], "ts": 0.0}
UNIVERSE_TTL = 1800  # segundos


def get_universe() -> list:
    now = time.time()
    if now - _universe["ts"] > UNIVERSE_TTL or not _universe["symbols"]:
        console.print("[dim]Actualizando universo de pares Binance...[/dim]")
        try:
            top40 = get_top_pairs_by_volume(QUOTE, TOP_N_SCAN)
            _universe["symbols"] = top40[:TOP_N_TRADE]
            _universe["ts"]      = now
            console.print(f"  [cyan]Universo:[/cyan] {', '.join(_universe['symbols'])}")
        except Exception as e:
            log.error(f"Error actualizando universo: {e}")
            if not _universe["symbols"]:
                # Fallback manual si falla la primera vez
                _universe["symbols"] = [
                    f"BTC{QUOTE}", f"ETH{QUOTE}", f"BNB{QUOTE}", f"SOL{QUOTE}",
                    f"XRP{QUOTE}", f"DOGE{QUOTE}", f"ADA{QUOTE}", f"AVAX{QUOTE}",
                    f"TRX{QUOTE}", f"DOT{QUOTE}",
                ]
    return _universe["symbols"]


# ── Ciclo de análisis ────────────────────────────────────────────────────────

def run_cycle(timeframe: str) -> None:
    """
    Ejecuta un ciclo completo para el timeframe dado.
    timeframe : intervalo de velas Binance ("15m", "30m", "1h", "2h")
    """
    now = datetime.now().strftime("%H:%M:%S")
    console.print(f"\n[bold]── Ciclo {timeframe.upper()}  {now} ──[/bold]")

    symbols = get_universe()

    table = Table(title=f"Elliott {timeframe.upper()} — {now}")
    table.add_column("Par",      style="cyan",   min_width=10)
    table.add_column("Precio",   style="white",  min_width=10)
    table.add_column("Señal",    style="bold",   min_width=6)
    table.add_column("SL",       style="red",    min_width=10)
    table.add_column("TP",       style="green",  min_width=10)
    table.add_column("Estado",   style="white",  min_width=14)

    exits_this_cycle = []
    new_entries      = []
    errors           = []

    # ── 1. Comprobar salidas (SL/TP) de todas las posiciones abiertas ────────
    for sym in list(trader.risk.open_positions.keys()):
        try:
            result = trader.check_exits(sym)
            if result.get("closed"):
                et    = result["exit_type"]
                pnl   = result["pnl"]
                color = "green" if pnl > 0 else "red"
                console.print(
                    f"  [{color}]{'✓ TP' if et == 'TP' else '✗ SL'} {sym}[/{color}]  "
                    f"[bold {color}]${pnl:+.2f}[/bold {color}]"
                )
                exits_this_cycle.append(sym)
        except Exception as e:
            log.error(f"check_exits {sym}: {e}")

    # ── 2. Buscar nuevas señales ─────────────────────────────────────────────
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, interval=timeframe, limit=OHLCV_LIMIT)
            if df.empty or len(df) < 50:
                continue

            signal = elliott.get_signal(df)
            price  = float(df["close"].iloc[-1])

            if signal["signal"] == "BUY":
                result = trader.execute_signal(sym, signal)
                if result.get("executed"):
                    estado    = "[green]✓ ABIERTO[/green]"
                    sig_color = "green"
                    new_entries.append(sym)
                else:
                    estado    = f"[dim]{result.get('reason', 'Rechazado')[:18]}[/dim]"
                    sig_color = "green"
                sl_str = f"${signal['sl']:,.2f}"
                tp_str = f"${signal['tp']:,.2f}"
            else:
                sig_color = "yellow"
                sl_str    = "—"
                tp_str    = "—"
                # Mostrar estado de posición abierta si existe
                if sym in trader.risk.open_positions:
                    pos = trader.risk.open_positions[sym]
                    unrealized = (price - pos["price"]) * pos["shares"]
                    color = "green" if unrealized >= 0 else "red"
                    estado = f"[{color}]Abierta ${unrealized:+.2f}[/{color}]"
                    sl_info = trader._sl_tp.get(sym)
                    if sl_info:
                        sl_str = f"${sl_info['sl']:,.2f}"
                        tp_str = f"${sl_info['tp']:,.2f}"
                else:
                    estado = "— Espera"

            table.add_row(
                sym,
                f"${price:,.4f}" if price < 10 else f"${price:,.2f}",
                f"[{sig_color}]{signal['signal']}[/{sig_color}]",
                sl_str, tp_str, estado,
            )

        except Exception as e:
            errors.append(f"{sym}: {str(e)[:40]}")
            log.error(f"Ciclo {timeframe} {sym}: {e}")

    console.print(table)

    # ── 3. Resumen ───────────────────────────────────────────────────────────
    status = trader.risk.get_status()
    pnl_color = "green" if status["pnl_total"] >= 0 else "red"
    console.print(
        f"[bold]Portfolio:[/bold] "
        f"Capital [cyan]${status['capital']:,.2f}[/cyan] | "
        f"PnL [{pnl_color}]${status['pnl_total']:+,.2f}[/{pnl_color}] | "
        f"DD {status['drawdown']:.1%} | "
        f"Posiciones {status['positions']}/{3} | "
        f"Trades totales {len(trader.trades)}"
    )
    if errors:
        console.print(f"[dim red]Errores: {' | '.join(errors[:3])}[/dim red]")


# ── Programación de ciclos ────────────────────────────────────────────────────

def _schedule_all() -> None:
    for minutes, tf in CYCLE_TIMEFRAMES.items():
        schedule.every(minutes).minutes.do(run_cycle, timeframe=tf)
        log.info(f"Ciclo programado: cada {minutes} min → velas {tf}")


# ── Arranque ──────────────────────────────────────────────────────────────────

def main() -> None:
    console.print("[bold cyan]" + "═" * 54 + "[/bold cyan]")
    console.print("[bold cyan]  QUANTBOT — Elliott Crypto  (paper trading)[/bold cyan]")
    console.print("[bold cyan]" + "═" * 54 + "[/bold cyan]")
    mode_label = "[bold red]LIVE — DINERO REAL[/bold red]" if TRADING_MODE == "live" else "[yellow]PAPER — simulado[/yellow]"
    console.print(
        f"  Modo       : {mode_label}\n"
        f"  Estrategia : Elliott Wave Proxy  (PF 3.13 · WR 63 %)\n"
        f"  Universo   : top-{TOP_N_SCAN} por volumen → opera los {TOP_N_TRADE} más líquidos\n"
        f"  Quote      : {QUOTE}\n"
        f"  Ciclos     : 15 min (15m) · 30 min (30m) · 60 min (1h) · 90 min (1h)\n"
    )

    # Refrescar universo y lanzar ciclo inicial en todos los timeframes
    get_universe()
    for tf in set(CYCLE_TIMEFRAMES.values()):
        run_cycle(tf)

    _schedule_all()

    console.print(
        "\n[bold green]Bot activo.[/bold green] "
        "Próximos ciclos: 15 min · 30 min · 60 min · 90 min\n"
        "[dim]Ctrl+C para detener[/dim]\n"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
