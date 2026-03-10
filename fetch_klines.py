import requests
import csv
import time
from datetime import datetime, timezone

# =============================================================================
# CONFIGURATION
# =============================================================================

SYMBOL   = "ETHUSDT"   # Options: BTCUSDT, ETHUSDT, ADAUSDT, BTCUSDC
INTERVAL = "4h"        # Options: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M

# Leave START_DATE as None to fetch the full available history,
# or set a date string "YYYY-MM-DD" to start from a specific point.
START_DATE = None      # Example: "2022-01-01"

# =============================================================================

BASE_URL       = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"
LIMIT          = 1000  # Max rows per request (Binance cap)

COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "num_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]


def ts_to_dt(ms: int) -> str:
    """Convert a millisecond UTC timestamp to a human-readable string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fetch_klines_batch(symbol: str, interval: str, start_ms: int) -> list:
    """Fetch up to LIMIT klines starting from start_ms (epoch ms)."""
    params = {
        "symbol":    symbol,
        "interval":  interval,
        "startTime": start_ms,
        "limit":     LIMIT,
    }
    response = requests.get(BASE_URL + KLINES_ENDPOINT, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_all_klines(symbol: str, interval: str, start_ms: int) -> list[dict]:
    """Page through the Binance klines endpoint until no more data is returned."""
    all_rows = []
    current_start = start_ms

    print(f"Fetching {symbol} [{interval}] from {ts_to_dt(current_start)} …")

    while True:
        batch = fetch_klines_batch(symbol, interval, current_start)

        if not batch:
            break

        for k in batch:
            all_rows.append({
                "open_time":             ts_to_dt(k[0]),
                "open":                  k[1],
                "high":                  k[2],
                "low":                   k[3],
                "close":                 k[4],
                "volume":                k[5],
                "close_time":            ts_to_dt(k[6]),
                "quote_asset_volume":    k[7],
                "num_trades":            k[8],
                "taker_buy_base_volume": k[9],
                "taker_buy_quote_volume": k[10],
            })

        print(f"  Fetched {len(all_rows):>7} candles — last: {all_rows[-1]['open_time']}")

        if len(batch) < LIMIT:
            # We've reached the end of available data
            break

        # Advance past the last candle's close time (+1 ms to avoid duplicates)
        current_start = batch[-1][6] + 1

        # Be polite to the API
        time.sleep(0.2)

    return all_rows


def export_to_csv(rows: list[dict], filename: str) -> None:
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    # Determine the start timestamp
    if START_DATE:
        start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = int(start_dt.timestamp() * 1000)
    else:
        # Binance launched in 2017-07-01; use that as a safe earliest date
        start_ms = int(datetime(2017, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)

    rows = fetch_all_klines(SYMBOL, INTERVAL, start_ms)

    if not rows:
        print("No data returned. Check SYMBOL and INTERVAL values.")
        return

    filename = f"{SYMBOL}_{INTERVAL}.csv"
    export_to_csv(rows, filename)
    print(f"\nDone! {len(rows)} candles saved to '{filename}'")


if __name__ == "__main__":
    main()
