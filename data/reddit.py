
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
