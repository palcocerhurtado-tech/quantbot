
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from execution.executor import PaperTrader

SYMBOL = "AAPL"

print("=" * 50)
print("PIPELINE COMPLETO DE TRADING")
print("=" * 50)

# 1. Datos
print("\n[1] Descargando datos...")
df   = fetch_ohlcv(SYMBOL, interval="1d", period="180d")
sent = get_news_sentiment(SYMBOL)
df   = add_technical_features(df)
df   = add_sentiment_features(df, sent)
print(f"    {len(df)} filas con {len(df.columns)} features")

# 2. Modelo
print("\n[2] Entrenando modelo...")
predictor = TradingPredictor(SYMBOL)
stats     = predictor.train(df)
print(f"    Accuracy CV: {stats['accuracy_cv']}")

# 3. Señal
print("\n[3] Generando señal...")
signal = predictor.predict(df)
print(f"    Señal     : {signal['signal']}")
print(f"    Confianza : {signal['confidence']:.1%}")
print(f"    Prob subir: {signal['prob_up']:.1%}")

# 4. Ejecución
print("\n[4] Ejecutando trade (paper)...")
trader = PaperTrader()
result = trader.execute_signal(SYMBOL, signal)
if result["executed"]:
    t = result["trade"]
    print(f"    Acción    : {t['action']} {t['symbol']}")
    print(f"    Precio    : ${t['price']:.2f}")
    print(f"    Invertido : ${t['size_usd']:.2f}")
    print(f"    Shares    : {t['shares']:.4f}")
else:
    print(f"    No ejecutado: {result['reason']}")

# 5. Portfolio
print("\n[5] Estado del portfolio...")
portfolio = trader.get_portfolio()
status    = portfolio["status"]
print(f"    Capital   : ${status['capital']:.2f}")
print(f"    Drawdown  : {status['drawdown']:.1%}")
print(f"    Trades    : {portfolio['n_trades']}")
print(f"    Posiciones: {portfolio['positions']}")

print("\n" + "=" * 50)
print("SISTEMA COMPLETO FUNCIONANDO")
print("=" * 50)
