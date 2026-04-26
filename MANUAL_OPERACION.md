# QUANTBOT — Manual de Operación
**Versión:** 1.0 · **Estrategia:** Elliott Wave Proxy · **Exchange:** Binance Spot

---

## ÍNDICE

1. [Arranque y parada](#1-arranque-y-parada)
2. [Configuración (.env)](#2-configuración-env)
3. [Modos de operación](#3-modos-de-operación)
4. [Comandos del bot](#4-comandos-del-bot)
5. [Ciclos automáticos](#5-ciclos-automáticos)
6. [Métricas en pantalla](#6-métricas-en-pantalla)
7. [Backtesting](#7-backtesting)
8. [Parámetros de riesgo](#8-parámetros-de-riesgo)
9. [Referencia rápida](#9-referencia-rápida)

---

## 1. ARRANQUE Y PARADA

### Arranque estándar (recomendado)

```bash
bash arrancar.sh
```

Este script hace automáticamente:
1. Descarga los últimos cambios de GitHub
2. Activa el entorno virtual
3. Ejecuta `python main.py`

### Arranque manual

```bash
source .venv/bin/activate
python main.py
```

### Parada del bot

```
Ctrl + C
```

El bot no tiene parada gradual. Al pulsar `Ctrl+C` se detienen todos los ciclos. **Las posiciones abiertas en modo live permanecen abiertas en Binance** — hay que cerrarlas manualmente desde Binance o relanzar el bot para que las gestione.

---

## 2. CONFIGURACIÓN (.env)

El archivo `.env` en la raíz del proyecto controla todo el comportamiento del bot.

### Variables obligatorias para modo live

| Variable | Ejemplo | Descripción |
|---|---|---|
| `TRADING_MODE` | `live` | `paper` = simulado · `live` = órdenes reales |
| `BINANCE_API_KEY` | `abc123...` | Clave pública de la API de Binance |
| `BINANCE_SECRET_KEY` | `xyz789...` | Clave secreta de la API de Binance |

### Variables de capital y riesgo

| Variable | Por defecto | Descripción |
|---|---|---|
| `INITIAL_CAPITAL` | `10000` | Capital inicial en USD (solo paper; en live se lee de Binance) |
| `RISK_PER_TRADE` | `0.01` | Riesgo por operación — `0.01` = 1% del capital |
| `MAX_POSITION_PCT` | `0.10` | Tamaño máximo por posición — `0.10` = 10% del capital |
| `MAX_DRAWDOWN_PCT` | `0.10` | El bot pausa nuevas entradas si el drawdown supera este umbral |
| `MAX_POSITIONS` | `3` | Máximo de posiciones abiertas simultáneamente |

### Variables del universo

| Variable | Por defecto | Descripción |
|---|---|---|
| `QUOTE_ASSET` | `USDT` | Moneda de cotización de los pares |
| `TOP_N_SCAN` | `40` | Pares de Binance a escanear por volumen 24h |
| `TOP_N_TRADE` | `10` | De los escaneados, los N más líquidos con los que operar |

### Variables opcionales

| Variable | Por defecto | Descripción |
|---|---|---|
| `NEWS_API_KEY` | — | Clave de NewsAPI.org (no requerida, se omite si no está) |
| `REDDIT_CLIENT_ID` | — | ID de app Reddit (opcional) |
| `REDDIT_CLIENT_SECRET` | — | Secret de app Reddit (opcional) |

### Ejemplo de .env completo

```env
TRADING_MODE=live
BINANCE_API_KEY=tu_api_key_aqui
BINANCE_SECRET_KEY=tu_secret_key_aqui

INITIAL_CAPITAL=10000
RISK_PER_TRADE=0.01
MAX_POSITION_PCT=0.10
MAX_DRAWDOWN_PCT=0.10
MAX_POSITIONS=3

QUOTE_ASSET=USDT
TOP_N_SCAN=40
TOP_N_TRADE=10
```

---

## 3. MODOS DE OPERACIÓN

### PAPER (simulado)

```env
TRADING_MODE=paper
```

- No se ejecutan órdenes reales
- El capital parte de `INITIAL_CAPITAL`
- Ideal para validar la estrategia antes de arriesgar dinero
- Los PnL son simulados con precio de mercado real

### LIVE (real)

```env
TRADING_MODE=live
```

- Se ejecutan órdenes **MARKET BUY/SELL reales en Binance Spot**
- El capital se sincroniza desde el balance USDT real de Binance al arrancar
- Requiere `BINANCE_API_KEY` y `BINANCE_SECRET_KEY` con permisos de **Spot Trading**
- El SL y TP se gestionan internamente (no son stop-orders en Binance)

> **AVISO:** En modo live el bot opera con dinero real. Revisa siempre los parámetros de riesgo antes de activarlo.

---

## 4. COMANDOS DEL BOT

El bot no tiene CLI interactiva — se controla mediante el archivo `.env` y la API de Python. Los siguientes métodos están disponibles para integración o scripts externos.

### 4.1 Ejecutar una señal manualmente

```python
from execution.executor import PaperTrader  # o LiveTrader

trader = PaperTrader()
resultado = trader.execute_signal("BTCUSDT", {
    "signal":     "BUY",
    "confidence": 0.80,
    "entry":      65000.0,
    "sl":         63500.0,
    "tp":         68000.0,
})
```

**Respuesta exitosa:**
```python
{
  "executed": True,
  "trade": {
    "symbol":         "BTCUSDT",
    "action":         "BUY",
    "price":          65000.0,
    "size_usd":       975.61,
    "shares":         0.01501,
    "confidence":     0.8,
    "timestamp":      "2024-01-15T10:30:00",
    "capital_before": 10000.0,
    "sl":             63500.0,
    "tp":             68000.0,
  }
}
```

**Respuesta rechazada:**
```python
{"executed": False, "reason": "Máximo de posiciones concurrentes alcanzado (3)"}
```

**Motivos de rechazo posibles:**

| Motivo | Causa |
|---|---|
| `Señal HOLD` | La señal no es BUY |
| `Confianza insuficiente: X%` | Confidence < 50% |
| `Drawdown máximo alcanzado: X%` | DD supera `MAX_DRAWDOWN_PCT` |
| `Ya hay posición abierta en {symbol}` | Posición duplicada |
| `Máximo de posiciones concurrentes alcanzado (N)` | Se alcanzó `MAX_POSITIONS` |
| `Capital insuficiente (<$50)` | Capital disponible muy bajo |
| `Precio no disponible` | Error en Binance API |
| `Size = 0` | Tamaño calculado nulo |

---

### 4.2 Comprobar salidas (SL / TP)

```python
resultado = trader.check_exits("BTCUSDT")
```

**Sin posición abierta:**
```python
{"checked": False}
```

**Posición cerrada por SL o TP:**
```python
{
  "checked":     True,
  "closed":      True,
  "symbol":      "BTCUSDT",
  "exit_type":   "TP",          # "SL" o "TP"
  "entry_price": 65000.0,
  "exit_price":  68000.0,
  "pnl":         +37.53,
  "timestamp":   "2024-01-15T11:00:00",
}
```

**Posición abierta sin tocar SL/TP:**
```python
{
  "checked":       True,
  "closed":        False,
  "current_price": 66500.0,
  "sl":            63500.0,
  "tp":            68000.0,
  "unrealized_pnl": +22.52,
}
```

---

### 4.3 Cerrar posición manualmente

```python
resultado = trader.close_position("BTCUSDT")
```

**Exitoso:**
```python
{
  "symbol":      "BTCUSDT",
  "entry_price": 65000.0,
  "exit_price":  66800.0,
  "pnl":         +27.01,
  "timestamp":   "2024-01-15T11:30:00",
}
```

**Sin posición:**
```python
{"closed": False, "reason": "Sin posición"}
```

---

### 4.4 Ver cartera completa

```python
cartera = trader.get_portfolio()
```

**Respuesta:**
```python
{
  "status": {
    "capital":   10250.0,
    "peak":      10250.0,
    "drawdown":  0.0,
    "positions": 1,
    "pnl_total": +250.0,
  },
  "positions": ["BTCUSDT"],
  "positions_detail": {
    "BTCUSDT": {
      "entry":      65000.0,
      "current":    66500.0,
      "unrealized": +22.52,
      "sl":         63500.0,
      "tp":         68000.0,
    }
  },
  "n_trades": 4,
}
```

---

### 4.5 Ver métricas de rendimiento

```python
metricas = trader.get_stats()
```

**Respuesta:**
```python
{
  "total_trades":  12,
  "win_rate":      0.6364,     # 63.64%
  "profit_factor": 3.13,
  "expectancy":    +42.50,     # USD por trade
  "avg_win":       +215.0,
  "avg_loss":      -68.0,
  "gross_profit":  1720.0,
  "gross_loss":    550.0,
  "best_trade":    +430.0,
  "worst_trade":   -95.0,
  "return_pct":    0.0521,     # 5.21%
  "open_trades":   1,
}
```

**Interpretación del Profit Factor:**

| PF | Calidad |
|---|---|
| < 1.0 | Sistema perdedor |
| 1.0 – 1.5 | Marginal |
| 1.5 – 2.0 | Bueno |
| 2.0 – 3.0 | Muy bueno |
| > 3.0 | Excelente (backtest: 3.13) |

---

### 4.6 Consultar balance real de Binance

```python
from data.market import get_account_snapshot

snapshot = get_account_snapshot()
```

**Respuesta:**
```python
{
  "balances": [
    {
      "asset":      "USDT",
      "free":       9750.50,
      "locked":     0.0,
      "total":      9750.50,
      "price_usdt": 1.0,
      "value_usdt": 9750.50,
    },
    {
      "asset":      "BTC",
      "free":       0.01501,
      "locked":     0.0,
      "total":      0.01501,
      "price_usdt": 65000.0,
      "value_usdt": 975.65,
    },
  ],
  "total_usdt": 10726.15,
  "updated_at": "10:30:45",
}
```

---

### 4.7 Consultar precio actual

```python
from data.market import get_latest_price

precio = get_latest_price("BTCUSDT")   # → 65432.10
```

Devuelve `0.0` si hay error de conexión.

---

### 4.8 Obtener universo activo

```python
from data.market import get_top_pairs_by_volume

pares = get_top_pairs_by_volume(quote="USDT", top_n=40)
# → ["BTCUSDT", "ETHUSDT", "BNBUSDT", ...]
```

Excluye stablecoins, tokens apalancados (UP/DOWN/BULL/BEAR) y tokens con caracteres no ASCII.

---

### 4.9 Descargar velas OHLCV

```python
from data.market import fetch_ohlcv

df = fetch_ohlcv("BTCUSDT", interval="1h", limit=300)
```

**Intervalos válidos:** `"1m"`, `"5m"`, `"15m"`, `"30m"`, `"1h"`, `"2h"`, `"4h"`, `"1d"`

Devuelve `pd.DataFrame` con columnas: `open`, `high`, `low`, `close`, `volume`

---

### 4.10 Obtener señal de la estrategia Elliott

```python
from models.elliott_strategy import ElliottStrategy

elliott = ElliottStrategy()
señal = elliott.get_signal(df)   # df = DataFrame OHLCV
```

**Respuesta:**
```python
{
  "signal":     "BUY",     # o "HOLD"
  "confidence": 0.80,
  "entry":      65000.0,
  "sl":         63500.0,
  "tp":         68000.0,
  "stop_dist":  1500.0,
  "rr":         2.0,
}
```

---

## 5. CICLOS AUTOMÁTICOS

El bot ejecuta ciclos de análisis de forma automática:

| Frecuencia | Velas analizadas | Acción |
|---|---|---|
| Cada **15 min** | 15m | Escanea señales Elliott en 15m |
| Cada **30 min** | 30m | Escanea señales Elliott en 30m |
| Cada **60 min** | 1h | Escanea señales Elliott en 1h |
| Cada **90 min** | 1h | Escanea señales Elliott en 1h |

**Cada ciclo hace lo siguiente:**
1. Refresca el universo de pares si han pasado más de 30 min
2. Comprueba SL/TP de todas las posiciones abiertas
3. Busca señales BUY en los pares sin posición abierta
4. Abre posición si pasa todos los filtros de riesgo
5. Muestra tabla de señales + cuenta Binance + métricas de rendimiento

---

## 6. MÉTRICAS EN PANTALLA

Cada ciclo imprime tres bloques en la terminal:

### Tabla de señales

```
┌─────────────┬──────────┬───────┬────────────┬────────────┬──────────────┐
│ Par         │ Precio   │ Señal │ SL         │ TP         │ Estado       │
├─────────────┼──────────┼───────┼────────────┼────────────┼──────────────┤
│ BTCUSDT     │ $65432   │ BUY   │ $63500     │ $68000     │ ✓ ABIERTO    │
│ ETHUSDT     │ $3421.50 │ HOLD  │ —          │ —          │ — Espera     │
└─────────────┴──────────┴───────┴────────────┴────────────┴──────────────┘
```

### Tabla de cuenta Binance

Muestra el balance real en tiempo real de todos los activos con saldo > 0.

### Tabla de métricas

```
Métricas de la sesión
Trades cerrados      12    Posiciones abiertas    1
Win Rate          63.6%    Profit Factor       3.13
Expectancy/trade  +42.50   Retorno sesión      +5.21%
Avg Win          $215.00   Avg Loss           -$68.00
Mejor trade      +$430.00  Peor trade          -$95.00
Gross Profit    $1720.00   Gross Loss          $550.00
Drawdown           2.10%   Capital          $10,521.00
```

**Código de colores:**
- Profit Factor: verde ≥ 1.5 · amarillo ≥ 1.0 · rojo < 1.0
- Drawdown: verde < 5% · amarillo < 10% · rojo ≥ 10%
- Win Rate: verde ≥ 50% · amarillo < 50%

---

## 7. BACKTESTING

### Ejecutar backtest de estrategias

```bash
python backtest_strategy_hunt.py --months 50 --rr 2.0 --risk 0.01 --target-pf 1.5
```

**Parámetros disponibles:**

| Parámetro | Por defecto | Descripción |
|---|---|---|
| `--symbol` | `BTC-USD` | Ticker de yfinance |
| `--months` | `50` | Meses de datos históricos |
| `--rr` | `2.0` | Ratio riesgo/beneficio (1:N) |
| `--risk` | `0.01` | Riesgo por operación (fracción) |
| `--capital` | `10000` | Capital inicial |
| `--target-pf` | `1.5` | PF mínimo para marcar como ganadora |
| `--commission` | `0.001` | Comisión por lado (0.001 = 0.1%) |

**Ejemplo con parámetros personalizados:**
```bash
python backtest_strategy_hunt.py \
  --symbol ETH-USD \
  --months 36 \
  --rr 3.0 \
  --risk 0.02 \
  --target-pf 2.0
```

**Resultado del backtest ganador (Elliott Wave Proxy sobre BTC — 50 meses):**

| Métrica | Resultado |
|---|---|
| Profit Factor | **3.13** |
| Win Rate | **63.64%** |
| Trades totales | 22 |
| Retorno | +186% |
| Max Drawdown | -12.3% |

---

## 8. PARÁMETROS DE RIESGO

### Cálculo del tamaño de posición

El bot usa **riesgo fijo por operación basado en SL**:

```
riesgo_USD    = capital_actual × RISK_PER_TRADE
stop_pct      = |precio_entrada - SL| / precio_entrada
tamaño_USD    = min(riesgo_USD / stop_pct, capital × MAX_POSITION_PCT)
```

**Ejemplo con BTC:**
- Capital: $10,000
- RISK_PER_TRADE: 1% → riesgo = $100
- Entrada: $65,000 · SL: $63,500 → stop_pct = 2.31%
- Tamaño = $100 / 2.31% = **$4,329** (limitado al 10% = $1,000)

### Filtros de protección activos

| Filtro | Parámetro | Acción |
|---|---|---|
| Drawdown máximo | `MAX_DRAWDOWN_PCT=10%` | Pausa todas las entradas |
| Posiciones simultáneas | `MAX_POSITIONS=3` | Rechaza nuevas entradas |
| Capital mínimo | $50 | Rechaza si capital < $50 |
| Confianza mínima | 50% | Rechaza señales de baja confianza |
| Posición duplicada | — | Rechaza si ya hay posición en ese par |

---

## 9. REFERENCIA RÁPIDA

### Comandos de terminal más usados

```bash
# Arrancar el bot
bash arrancar.sh

# Arrancar manualmente
source .venv/bin/activate && python main.py

# Ejecutar backtest
python backtest_strategy_hunt.py --months 50 --rr 2.0

# Ver logs en tiempo real
tail -f logs/trades.log

# Ver estado del repositorio
git status

# Actualizar el código
git fetch origin
git checkout origin/claude/review-bot-parameters-6MRJk -- .
```

### Cambiar de paper a live

1. Abrir el archivo `.env`
2. Cambiar `TRADING_MODE=paper` → `TRADING_MODE=live`
3. Asegurarse de que `BINANCE_API_KEY` y `BINANCE_SECRET_KEY` están configuradas
4. Reiniciar el bot: `bash arrancar.sh`

### Ajustar el riesgo

Editar `.env` y reiniciar:

```env
RISK_PER_TRADE=0.005    # Bajar a 0.5% para más conservador
MAX_POSITIONS=2         # Reducir posiciones simultáneas
MAX_DRAWDOWN_PCT=0.05   # Parar si drawdown supera el 5%
```

### Ampliar el universo de pares

```env
TOP_N_SCAN=60     # Escanear los 60 pares más volumizados
TOP_N_TRADE=15    # Operar los 15 más líquidos
```

---

*Documento generado automáticamente por Claude Code · QuantBot v1.0*
