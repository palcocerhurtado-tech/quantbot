
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from data.reddit import get_reddit_sentiment
from models.features import add_technical_features, add_sentiment_features

print("=== TEST 1: Sentimiento de noticias ===")
news_sent = get_news_sentiment("AAPL")
print(f"  Compound : {news_sent['compound']:.3f}")
print(f"  Positivo : {news_sent['positive']:.3f}")
print(f"  Negativo : {news_sent['negative']:.3f}")

print("\n=== TEST 2: Sentimiento de Reddit ===")
reddit_sent = get_reddit_sentiment("AAPL")
print(f"  Compound : {reddit_sent['compound']:.3f}")
print(f"  Posts    : {reddit_sent['num_posts']}")

print("\n=== TEST 3: Features técnicas ===")
df = fetch_ohlcv("AAPL", interval="1d", period="60d")
df = add_technical_features(df)
df = add_sentiment_features(df, news_sent)
print(f"  Filas    : {len(df)}")
print(f"  Columnas : {len(df.columns)}")
print(f"  RSI actual     : {df['rsi'].iloc[-1]:.1f}")
print(f"  MACD actual    : {df['macd'].iloc[-1]:.3f}")
print(f"  Return 1d      : {df['return_1d'].iloc[-1]*100:.2f}%")
print(f"  Sentimiento    : {df['sent_compound'].iloc[-1]:.3f}")
print("\n=== TODOS LOS MÓDULOS FUNCIONAN ===")
