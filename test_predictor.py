
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor

SYMBOL = "AAPL"

print(f"=== Entrenando modelo para {SYMBOL} ===")
df      = fetch_ohlcv(SYMBOL, interval="1d", period="180d")
sent    = get_news_sentiment(SYMBOL)
df      = add_technical_features(df)
df      = add_sentiment_features(df, sent)

predictor = TradingPredictor(SYMBOL)
stats     = predictor.train(df)

print(f"  Muestras      : {stats.get('n_samples')}")
print(f"  Features      : {stats.get('n_features')}")
print(f"  Accuracy CV   : {stats.get('accuracy_cv')}")

print(f"\n=== Generando señal actual ===")
signal = predictor.predict(df)
print(f"  Símbolo    : {signal['symbol']}")
print(f"  Señal      : {signal['signal']}")
print(f"  Confianza  : {signal['confidence']:.1%}")
print(f"  Prob subir : {signal['prob_up']:.1%}")
print(f"  Prob bajar : {signal['prob_down']:.1%}")
print("\n=== MODELO FUNCIONANDO ===")
