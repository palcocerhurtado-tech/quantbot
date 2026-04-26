# QuantBot Legacy

Bot de trading algoritmico para criptomonedas en Binance Spot. Escanea el mercado por liquidez, aplica una estrategia `Elliott Wave Proxy`, gestiona entradas con riesgo fijo y controla salidas con `SL/TP`.

> Aviso: este proyecto puede operar dinero real si `TRADING_MODE=live`. Usalo con claves sin permisos de retirada, capital pequeno al principio y bajo tu responsabilidad.

## Que Hace

- Escanea los `top 40` pares de Binance por volumen.
- Opera los `10` pares mas liquidos del universo detectado.
- Ejecuta ciclos programados cada `15`, `30`, `60` y `90` minutos.
- Usa velas `15m`, `30m` y `1h` segun el ciclo.
- Puede funcionar en `paper` o `live`.
- Muestra estado de cuenta, posiciones, metricas, win rate, profit factor y drawdown.
- Guarda logs en `logs/runtime.out`.

## Estrategia Actual

La estrategia principal esta en [models/elliott_strategy.py](models/elliott_strategy.py).

Nombre: `Elliott Wave Proxy`

Logica resumida:

- Detecta `swing low` confirmado con 5 barras a cada lado.
- Filtra tendencia con `EMA 50 > EMA 200`.
- Exige que el precio este por encima de `EMA200 * 0.97`.
- Opera solo entradas long.
- Calcula `ATR 14`.
- Calcula stop y take profit con RR fijo.

Formulas:

```text
stop_dist = max(ATR_14 * 1.5, precio * 0.5%)
SL long   = entrada - stop_dist
TP long   = entrada + stop_dist * 2.0
qty       = (capital * 1%) / stop_dist
```

## Estructura

```text
config/settings.py          Configuracion general y variables de entorno
data/market.py              Datos publicos y privados de Binance
models/elliott_strategy.py  Estrategia Elliott Wave Proxy
execution/executor.py       PaperTrader y LiveTrader
execution/risk.py           Gestion de riesgo y posiciones
main.py                     Arranque, ciclos y consola
logs/runtime.out            Log principal de ejecucion
logs/backtests/             Resultados de backtesting
```

## Instalacion

Desde la carpeta del proyecto:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
```

## Configuracion

Crea o edita el archivo `.env` en la raiz del repo.

Modo paper:

```env
TRADING_MODE=paper
QUOTE_ASSET=USDT
INITIAL_CAPITAL=10000
RISK_PER_TRADE=0.01
MAX_POSITION_PCT=0.10
MAX_DRAWDOWN_PCT=0.10
MAX_POSITIONS=3
TOP_N_SCAN=40
TOP_N_TRADE=10
```

Modo live:

```env
TRADING_MODE=live
QUOTE_ASSET=USDT
BINANCE_API_KEY=tu_api_key
BINANCE_SECRET_KEY=tu_secret_key
INITIAL_CAPITAL=10000
RISK_PER_TRADE=0.01
MAX_POSITION_PCT=0.10
MAX_DRAWDOWN_PCT=0.10
MAX_POSITIONS=3
TOP_N_SCAN=40
TOP_N_TRADE=10
```

Opcional para noticias y Reddit:

```env
NEWS_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=quantbot/1.0
```

## Arrancar El Bot

Arranque recomendado en segundo plano:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
pkill -f "import main; main.main()" 2>/dev/null
nohup /Users/pabloalcocer/Desktop/quantbot-legacy/.venv/bin/python -c 'import main; main.main()' >> /Users/pabloalcocer/Desktop/quantbot-legacy/logs/runtime.out 2>&1 &
```

Ver logs en vivo:

```bash
tail -f /Users/pabloalcocer/Desktop/quantbot-legacy/logs/runtime.out
```

Comprobar si esta corriendo:

```bash
pgrep -fal "import main; main.main()"
```

Parar el bot:

```bash
pkill -f "import main; main.main()"
```

Arranque en primer plano, util para depurar:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
./.venv/bin/python main.py
```

## Comandos Habituales

Ver ultimas lineas del log:

```bash
tail -n 80 /Users/pabloalcocer/Desktop/quantbot-legacy/logs/runtime.out
```

Limpiar procesos duplicados y arrancar uno solo:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
pkill -f "import main; main.main()" 2>/dev/null
nohup /Users/pabloalcocer/Desktop/quantbot-legacy/.venv/bin/python -c 'import main; main.main()' >> logs/runtime.out 2>&1 &
pgrep -fal "import main; main.main()"
```

Probar instalacion basica:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
./.venv/bin/python test_setup.py
```

Ejecutar tests:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
./.venv/bin/python -m unittest discover -s tests -v
```

## Backtesting

Busqueda de estrategias en 50 meses:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
PYTHONUNBUFFERED=1 ./.venv/bin/python backtest_strategy_hunt.py --symbol BTCUSDT --interval 15m --months 50 --risk-pct 0.01 --target-pf 1.5 --min-trades 50 --output logs/backtests/strategy_hunt_50m_final.csv
```

Backtest BTC con RR fijo:

```bash
cd /Users/pabloalcocer/Desktop/quantbot-legacy
./.venv/bin/python backtest_btc_rr_fixed.py --symbol BTCUSDT --months 50 --risk-pct 0.01 --rr 2.0
```

Resultados habituales:

```text
logs/backtests/strategy_hunt_50m_final.csv
logs/backtests/strategy_hunt_50m.csv
logs/backtests/resultados_rr_fixed.txt
```

## Seguridad

- No subas `.env` a GitHub.
- Usa claves de Binance Spot con permisos minimos.
- No actives permisos de retirada.
- Empieza en `TRADING_MODE=paper`.
- En `live`, usa poco capital hasta validar estabilidad.
- Si una clave API se comparte por error, rotala en Binance.
- Revisa `logs/runtime.out` despues de arrancar.

## Notas Operativas

- `Ctrl+C` dentro de `tail -f` solo cierra la vista del log; no para el bot.
- Para parar el bot usa `pkill -f "import main; main.main()"`.
- Si arrancas el bot dos veces, tendras dos procesos; conviene ejecutar `pkill` antes de arrancarlo.
- Si Binance muestra capital muy bajo, el bot puede estar en `LIVE` pero no abrir operaciones por minimos de mercado.

## Disclaimer

Este repositorio es software experimental de trading algoritmico. El backtesting no garantiza resultados futuros. El modo `live` puede generar perdidas reales.
