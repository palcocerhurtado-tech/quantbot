from data.market import fetch_ohlcv, get_latest_price
from logs.logger import get_logger

log = get_logger("test")

print("=== TEST 1: Descargando datos de AAPL ===")
df = fetch_ohlcv("AAPL", interval="1d", period="10d")
print(df.tail(3))
print(f"Total filas: {len(df)}")

print("\n=== TEST 2: Precio actual ===")
price = get_latest_price("AAPL")
print(f"Precio AAPL: ${price:.2f}")

print("\n=== TODO FUNCIONA CORRECTAMENTE ===")
