# DCA Backtester

Simulates a leveraged futures DCA strategy (paper trading) against historical Binance kline data. No live orders are ever placed.

---

## How it works

The strategy opens a futures position and adds more capital ("bullets") to it every N hours based on the position's current ROI on margin. When the position hits the take-profit target it closes and immediately reopens. If bullets run out before TP/SL, the sim holds and waits.

**Bullet:** a fixed unit of capital = `TOTAL_CAPITAL / TOTAL_BULLETS`

---

## Requirements

- Python 3.10+
- `requests` (only needed for `fetch_klines.py`)

```bash
pip install requests
```

---

## Step 1 — Fetch historical data

Edit the constants at the top of `fetch_klines.py`:

| Constant | Description | Example |
|---|---|---|
| `SYMBOL` | Trading pair | `"BTCUSDT"` |
| `INTERVAL` | Candle timeframe | `"1h"`, `"4h"`, `"1d"` |
| `START_DATE` | Start date (`"YYYY-MM-DD"`) or `None` for full history | `"2022-01-01"` |

Then run:

```bash
python fetch_klines.py
```

This creates a file named `{SYMBOL}_{INTERVAL}.csv` (e.g. `BTCUSDT_1h.csv`) in the same directory. The CSV contains: `open_time`, `open`, `high`, `low`, `close`, `volume`, `close_time`, `quote_asset_volume`, `num_trades`, `taker_buy_base_volume`, `taker_buy_quote_volume`.

---

## Step 2 — Run the backtest

Edit the constants at the top of `dca_backtest.py`:

### Trading settings

| Constant | Description | Example |
|---|---|---|
| `DIRECTION` | Position direction | `"LONG"` or `"SHORT"` |
| `LEVERAGE` | Leverage multiplier | `10` |
| `TOTAL_CAPITAL` | Total USD to deploy across all bullets | `1000.0` |
| `TOTAL_BULLETS` | Number of bullets in the pool | `30` |
| `INITIAL_BULLETS` | Bullets used to open the first entry | `3` |
| `TP_PCT` | Take profit — close when ROI on margin ≥ this % | `15.0` |
| `SL_PCT` | Stop loss — close when ROI on margin ≤ −this % (`None` = off) | `50.0` or `None` |
| `BULLET_INTERVAL_H` | Hours between each DCA check | `24` |

### Data settings

| Constant | Description | Example |
|---|---|---|
| `PAIR` | Must match a downloaded CSV | `"BTCUSDT"` |
| `INTERVAL` | Must match a downloaded CSV | `"1h"` |
| `START_DATE` | Backtest start date `"YYYY-MM-DD"` | `"2020-03-01"` |
| `END_DATE` | Backtest end date `"YYYY-MM-DD"` | `"2021-04-10"` |

### DCA tier table

Controls how many bullets are added based on the current ROI. Evaluated top to bottom — first match wins.

```python
DCA_TIERS = [
    # (min_roi_pct,  bullets_to_position,  bullets_to_margin)
    (  0.0, 1, 0),           #  0% ≤ ROI < TP%    → +1 position bullet
    ( -5.0, 2, 0),           # -5% ≤ ROI < 0%     → +2 position bullets
    (-10.0, 3, 0),           # -10% ≤ ROI < -5%   → +3 position bullets
    (-15.0, 4, 0),           # -15% ≤ ROI < -10%  → +4 position bullets
    (-40.0, 5, 0),           # -40% ≤ ROI < -15%  → +5 position bullets
    (float("-inf"), 3, 3),   #  ROI < -40%         → +3 position + +3 isolated margin
]
```

- **bullets_to_position** — increases the notional size of the position and updates the average entry price
- **bullets_to_margin** — adds isolated margin only (pushes the liquidation price further away without changing the notional size)

Then run:

```bash
python dca_backtest.py
```

---

## Outputs

### Console summary

```
==========================================
           BACKTEST RESULTS
==========================================
  Period      : 2020-03-01 → 2021-04-10
  Pair        : BTCUSDT 1h  |  LONG  |  10x
  Capital     : $1,000.00  (30 bullets × $33.33)
------------------------------------------
  Trades      : 14  (12 wins, 2 losses)
  Win rate    : 85.7%
  Total PnL   : +$842.10  (+84.2%)
  Max drawdown: $310.00  (31.0%)
  Avg ROI/trade: +6.00%
  Liquidations: 0
==========================================
```

### `trades_log.csv`

One row per closed trade.

| Column | Description |
|---|---|
| `trade_id` | Sequential trade number |
| `entry_time` / `exit_time` | UTC timestamps |
| `direction` | LONG or SHORT |
| `avg_entry_price` | Weighted average entry across all DCA bullets |
| `exit_price` | Price at close |
| `bullets_used_position` | Total position bullets consumed |
| `bullets_used_margin` | Total margin bullets consumed |
| `total_margin_usd` | Total USD committed (position + isolated margin) |
| `pnl_usd` | Realized profit/loss in USD |
| `roi_pct` | ROI on margin at close |
| `exit_reason` | `TP`, `SL`, `LIQUIDATION`, or `END_OF_PERIOD` |

### `bullets_log.csv`

One row per bullet action (open, DCA, margin top-up).

| Column | Description |
|---|---|
| `timestamp` | UTC time of the action |
| `price` | Execution price |
| `action` | `OPEN`, `DCA_POSITION`, or `DCA_MARGIN` |
| `bullets_count` | Bullets added in this action |
| `pos_avg_entry` | Average entry price after this action |
| `roi_pct` | ROI at the time of this action |
| `liquidation_price` | Liquidation price after this action |
| `bullets_remaining` | Position bullets left in the pool |

---

## Notes

- All ROI percentages are **ROI on margin** (leverage-amplified), matching what exchanges display as unrealized ROI.
- Liquidation price uses a simplified isolated-margin formula with a 0.4% maintenance margin rate.
- The bullet pool resets to full after every TP, SL, or liquidation.
- CSV data files are excluded from the repo (`.gitignore`). Re-generate them with `fetch_klines.py`.
