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

from data.market import get_top_pairs_by_volume, fetch_ohlcv, get_latest_price, get_account_snapshot
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

    # Sincronizar capital real desde Binance al inicio de cada ciclo (solo live)
    if hasattr(trader, "_sync_capital"):
        trader._sync_capital()

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

    # ── 3. Cuenta real Binance ────────────────────────────────────────────────
    snap = get_account_snapshot()
    if snap.get("error"):
        console.print(f"[dim yellow]Cuenta Binance: {snap['error']}[/dim yellow]")
    else:
        acc_table = Table(title=f"Cuenta Binance — {snap['updated_at']}", show_header=True)
        acc_table.add_column("Activo",       style="cyan",  min_width=8)
        acc_table.add_column("Disponible",   style="white", min_width=14)
        acc_table.add_column("Bloqueado",    style="dim",   min_width=12)
        acc_table.add_column("Precio USDT",  style="white", min_width=14)
        acc_table.add_column("Valor USDT",   style="bold",  min_width=14)

        for b in snap["balances"]:
            val_color = "green" if b["value_usdt"] > 1 else "dim"
            price_str = f"${b['price_usdt']:,.4f}" if b["price_usdt"] < 100 else f"${b['price_usdt']:,.2f}"
            acc_table.add_row(
                b["asset"],
                f"{b['free']:.8f}".rstrip("0").rstrip("."),
                f"{b['locked']:.8f}".rstrip("0").rstrip(".") if b["locked"] > 0 else "—",
                price_str if b["asset"] not in ("USDT", "USDC", "BUSD") else "1.00",
                f"[{val_color}]${b['value_usdt']:,.4f}[/{val_color}]",
            )

        acc_table.add_row(
            "[bold]TOTAL[/bold]", "", "", "",
            f"[bold cyan]${snap['total_usdt']:,.2f}[/bold cyan]",
        )
        console.print(acc_table)

    # ── 4. Métricas del bot ──────────────────────────────────────────────────
    stats  = trader.get_stats()
    status = trader.risk.get_status()

    m_table = Table(title="Métricas de la sesión", show_header=False, box=None)
    m_table.add_column("Métrica", style="dim",   min_width=22)
    m_table.add_column("Valor",   style="bold",  min_width=14)
    m_table.add_column("Métrica", style="dim",   min_width=22)
    m_table.add_column("Valor",   style="bold",  min_width=14)

    pf_val   = stats["profit_factor"]
    pf_color = "green" if pf_val >= 1.5 else ("yellow" if pf_val >= 1.0 else "red")
    wr_color = "green" if stats["win_rate"] >= 0.5 else "yellow"
    ret_color = "green" if stats["return_pct"] >= 0 else "red"
    dd_color  = "green" if status["drawdown"] < 0.05 else ("yellow" if status["drawdown"] < 0.10 else "red")

    m_table.add_row(
        "Trades cerrados",   str(stats["total_trades"]),
        "Posiciones abiertas", str(stats["open_trades"]),
    )
    m_table.add_row(
        "Win Rate",  f"[{wr_color}]{stats['win_rate']:.1%}[/{wr_color}]",
        "Profit Factor", f"[{pf_color}]{pf_val:.2f}[/{pf_color}]",
    )
    m_table.add_row(
        "Expectancy/trade",  f"${stats['expectancy']:+.2f}",
        "Retorno sesión",  f"[{ret_color}]{stats['return_pct']:+.2%}[/{ret_color}]",
    )
    m_table.add_row(
        "Avg Win",  f"[green]${stats['avg_win']:,.2f}[/green]",
        "Avg Loss", f"[red]-${stats['avg_loss']:,.2f}[/red]",
    )
    m_table.add_row(
        "Mejor trade",  f"[green]${stats['best_trade']:+,.2f}[/green]",
        "Peor trade",   f"[red]${stats['worst_trade']:+,.2f}[/red]",
    )
    m_table.add_row(
        "Gross Profit",  f"[green]${stats['gross_profit']:,.2f}[/green]",
        "Gross Loss",    f"[red]${stats['gross_loss']:,.2f}[/red]",
    )
    mode_label = "[bold red]LIVE[/bold red]" if TRADING_MODE == "live" else "[yellow]PAPER[/yellow]"
    real_capital = snap.get("total_usdt", status["capital"]) if TRADING_MODE == "live" and not snap.get("error") else status["capital"]
    cap_label    = f"${real_capital:,.2f}" + (" [dim](Binance)[/dim]" if TRADING_MODE == "live" and not snap.get("error") else "")
    m_table.add_row(
        "Drawdown",  f"[{dd_color}]{status['drawdown']:.2%}[/{dd_color}]",
        "Capital",   cap_label,
    )
    m_table.add_row(
        "Modo",      mode_label,
        "PnL sesión", f"[{'green' if status['pnl_total'] >= 0 else 'red'}]${status['pnl_total']:+,.2f}[/{'green' if status['pnl_total'] >= 0 else 'red'}]",
    )
    console.print(m_table)

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
