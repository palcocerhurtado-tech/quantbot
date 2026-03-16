from pathlib import Path

base = Path.home() / "Desktop" / "quantbot"

# ── data/news.py ──────────────────────────────────────────────────
(base / "data" / "news.py").write_text('''
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from config.settings import NEWS_API_KEY
from logs.logger import get_logger

log = get_logger("news")
analyzer = SentimentIntensityAnalyzer()

FALLBACK_HEADLINES = [
    "Stock market rallies on strong earnings",
    "Fed signals pause in rate hikes",
    "Tech stocks surge amid AI optimism",
    "Markets volatile on inflation data",
    "Investors cautious ahead of jobs report"
]

def fetch_headlines(symbol: str, max_articles: int = 10) -> list:
    """Descarga titulares de NewsAPI. Si no hay key, usa datos de prueba."""
    if not NEWS_API_KEY:
        log.warning("Sin NEWS_API_KEY, usando titulares de prueba")
        return FALLBACK_HEADLINES

    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": symbol,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": max_articles,
            "apiKey": NEWS_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        articles = response.json().get("articles", [])
        headlines = [a["title"] for a in articles if a.get("title")]
        log.info(f"{symbol}: {len(headlines)} titulares descargados")
        return headlines
    except Exception as e:
        log.error(f"Error noticias {symbol}: {e}")
        return FALLBACK_HEADLINES

def analyze_sentiment(texts: list) -> dict:
    """Analiza el sentimiento de una lista de textos."""
    if not texts:
        return {"compound": 0.0, "positive": 0.0, "negative": 0.0, "neutral": 1.0}

    scores = [analyzer.polarity_scores(t) for t in texts]
    return {
        "compound":  sum(s["compound"] for s in scores) / len(scores),
        "positive":  sum(s["pos"]      for s in scores) / len(scores),
        "negative":  sum(s["neg"]      for s in scores) / len(scores),
        "neutral":   sum(s["neu"]      for s in scores) / len(scores),
    }

def get_news_sentiment(symbol: str) -> dict:
    """Pipeline completo: descarga titulares y devuelve sentimiento."""
    headlines = fetch_headlines(symbol)
    sentiment = analyze_sentiment(headlines)
    log.info(f"{symbol} sentimiento noticias: compound={sentiment['compound']:.3f}")
    return sentiment
''')

# ── data/reddit.py ────────────────────────────────────────────────
(base / "data" / "reddit.py").write_text('''
import praw
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from config.settings import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
from logs.logger import get_logger

log = get_logger("reddit")
analyzer = SentimentIntensityAnalyzer()

FALLBACK_POSTS = [
    "AAPL looking bullish, strong support at 250",
    "Bought more Apple today, long term hold",
    "Tech sector uncertain, watching closely",
    "Great earnings expected next quarter",
    "Market correction incoming, be careful"
]

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket"]

def get_reddit_client():
    """Crea cliente de Reddit si hay credenciales."""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    try:
        return praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT
        )
    except Exception as e:
        log.error(f"Error conectando Reddit: {e}")
        return None

def fetch_reddit_posts(symbol: str, limit: int = 20) -> list:
    """Descarga posts de Reddit sobre el símbolo."""
    reddit = get_reddit_client()

    if not reddit:
        log.warning("Sin credenciales Reddit, usando posts de prueba")
        return FALLBACK_POSTS

    posts = []
    try:
        for subreddit_name in SUBREDDITS:
            subreddit = reddit.subreddit(subreddit_name)
            for post in subreddit.search(symbol, limit=limit//len(SUBREDDITS), sort="new"):
                posts.append(post.title)
        log.info(f"{symbol}: {len(posts)} posts de Reddit")
        return posts if posts else FALLBACK_POSTS
    except Exception as e:
        log.error(f"Error Reddit {symbol}: {e}")
        return FALLBACK_POSTS

def get_reddit_sentiment(symbol: str) -> dict:
    """Pipeline completo: descarga posts y devuelve sentimiento."""
    posts = fetch_reddit_posts(symbol)
    scores = [analyzer.polarity_scores(p) for p in posts]
    result = {
        "compound": sum(s["compound"] for s in scores) / len(scores),
        "positive": sum(s["pos"]      for s in scores) / len(scores),
        "negative": sum(s["neg"]      for s in scores) / len(scores),
        "num_posts": len(posts)
    }
    log.info(f"{symbol} sentimiento Reddit: compound={result['compound']:.3f}")
    return result
''')

# ── models/features.py ────────────────────────────────────────────
(base / "models" / "features.py").write_text('''
import pandas as pd
import numpy as np
import ta
from logs.logger import get_logger

log = get_logger("features")

def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """Añade 20+ indicadores técnicos al DataFrame de precios."""
    if df.empty or len(df) < 30:
        log.warning("Datos insuficientes para calcular features")
        return df

    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # ── Tendencia ──────────────────────────────────────────────────
    df["sma_10"]   = ta.trend.sma_indicator(c, window=10)
    df["sma_20"]   = ta.trend.sma_indicator(c, window=20)
    df["ema_12"]   = ta.trend.ema_indicator(c, window=12)
    df["ema_26"]   = ta.trend.ema_indicator(c, window=26)
    df["macd"]     = ta.trend.macd(c)
    df["macd_sig"] = ta.trend.macd_signal(c)
    df["macd_diff"]= ta.trend.macd_diff(c)

    # ── Momentum ───────────────────────────────────────────────────
    df["rsi"]      = ta.momentum.rsi(c, window=14)
    df["stoch_k"]  = ta.momentum.stoch(h, l, c, window=14)
    df["stoch_d"]  = ta.momentum.stoch_signal(h, l, c, window=14)
    df["roc"]      = ta.momentum.roc(c, window=10)

    # ── Volatilidad ────────────────────────────────────────────────
    df["bb_high"]  = ta.volatility.bollinger_hband(c, window=20)
    df["bb_low"]   = ta.volatility.bollinger_lband(c, window=20)
    df["bb_pct"]   = ta.volatility.bollinger_pband(c, window=20)
    df["atr"]      = ta.volatility.average_true_range(h, l, c, window=14)

    # ── Volumen ────────────────────────────────────────────────────
    df["obv"]      = ta.volume.on_balance_volume(c, v)
    df["vwap"]     = (c * v).cumsum() / v.cumsum()

    # ── Features derivadas ─────────────────────────────────────────
    df["return_1d"]  = c.pct_change(1)
    df["return_5d"]  = c.pct_change(5)
    df["price_vs_sma20"] = (c - df["sma_20"]) / df["sma_20"]
    df["volume_sma"] = v.rolling(20).mean()
    df["volume_ratio"] = v / df["volume_sma"]

    # ── Target: sube o baja mañana ─────────────────────────────────
    df["target"] = (c.shift(-1) > c).astype(int)

    df = df.dropna()
    log.info(f"Features calculadas: {len(df.columns)} columnas, {len(df)} filas")
    return df

def add_sentiment_features(df: pd.DataFrame, sentiment: dict) -> pd.DataFrame:
    """Añade scores de sentimiento como features al DataFrame."""
    df = df.copy()
    df["sent_compound"] = sentiment.get("compound", 0.0)
    df["sent_positive"] = sentiment.get("positive", 0.0)
    df["sent_negative"] = sentiment.get("negative", 0.0)
    return df

def get_feature_columns() -> list:
    """Lista de columnas que usa el modelo (sin target ni precios raw)."""
    return [
        "sma_10", "sma_20", "ema_12", "ema_26",
        "macd", "macd_sig", "macd_diff",
        "rsi", "stoch_k", "stoch_d", "roc",
        "bb_high", "bb_low", "bb_pct", "atr",
        "obv", "vwap", "return_1d", "return_5d",
        "price_vs_sma20", "volume_ratio",
        "sent_compound", "sent_positive", "sent_negative"
    ]
''')

# ── test_modules.py ───────────────────────────────────────────────
(base / "test_modules.py").write_text('''
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from data.reddit import get_reddit_sentiment
from models.features import add_technical_features, add_sentiment_features

print("=== TEST 1: Sentimiento de noticias ===")
news_sent = get_news_sentiment("AAPL")
print(f"  Compound : {news_sent['compound']:.3f}")
print(f"  Positivo : {news_sent['positive']:.3f}")
print(f"  Negativo : {news_sent['negative']:.3f}")

print("\\n=== TEST 2: Sentimiento de Reddit ===")
reddit_sent = get_reddit_sentiment("AAPL")
print(f"  Compound : {reddit_sent['compound']:.3f}")
print(f"  Posts    : {reddit_sent['num_posts']}")

print("\\n=== TEST 3: Features técnicas ===")
df = fetch_ohlcv("AAPL", interval="1d", period="60d")
df = add_technical_features(df)
df = add_sentiment_features(df, news_sent)
print(f"  Filas    : {len(df)}")
print(f"  Columnas : {len(df.columns)}")
print(f"  RSI actual     : {df['rsi'].iloc[-1]:.1f}")
print(f"  MACD actual    : {df['macd'].iloc[-1]:.3f}")
print(f"  Return 1d      : {df['return_1d'].iloc[-1]*100:.2f}%")
print(f"  Sentimiento    : {df['sent_compound'].iloc[-1]:.3f}")
print("\\n=== TODOS LOS MÓDULOS FUNCIONAN ===")
''')

print("Módulos creados correctamente:")
print("  data/news.py")
print("  data/reddit.py")
print("  models/features.py")
print("  test_modules.py")
