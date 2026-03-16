
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
