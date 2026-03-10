import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DIRECTION         = "LONG"        # "LONG" or "SHORT"
LEVERAGE          = 5            # leverage multiplier (e.g. 10 = 10x)
TOTAL_CAPITAL     = 100.0        # USD — total capital to deploy across all bullets
TOTAL_BULLETS     = 30            # number of bullets in the pool
INITIAL_BULLETS   = 2             # bullets used to open the first entry

TP_PCT            = 20.0          # take profit: close when ROI on margin >= this %
SL_PCT            = 50.0          # stop loss:   close when ROI on margin <= -this % (None = off)

BULLET_INTERVAL_H = 24            # hours between each DCA check / bullet add

PAIR              = "ETHUSDT"
INTERVAL          = "4h"          # must match an existing {PAIR}_{INTERVAL}.csv file
START_DATE        = "2022-01-01"  # inclusive  "YYYY-MM-DD"
END_DATE          = "2026-03-9"  # inclusive  "YYYY-MM-DD"

# DCA tier table — evaluated top to bottom, first match wins.
# Each row: (lower_bound_roi_pct, bullets_to_add_to_position, bullets_to_add_as_margin)
#   bullets_to_add_to_position  → increases notional & moves avg entry
#   bullets_to_add_as_margin    → adds isolated margin only (pushes liq price, no notional change)
DCA_TIERS = [
    (  0.0, 1, 0),           #  0% ≤ ROI < TP%    → +1 position bullet
    ( -5.0, 2, 0),           # -5% ≤ ROI < 0%     → +2 position bullets
    (-10.0, 3, 0),           # -10% ≤ ROI < -5%   → +3 position bullets
    (-15.0, 4, 0),           # -15% ≤ ROI < -10%  → +4 position bullets
    (-20.0, 5, 0),           # -20% ≤ ROI < -15%  → +5 position bullets
    (-40.0, 6, 0),           # -40% ≤ ROI < -15%  → +5 position bullets
    (float("-inf"), 3, 3),   #  ROI < -40%         → +3 position + +3 margin
]

# Binance approximate maintenance margin rate (used in liquidation price calc)
MAINTENANCE_MARGIN_RATE = 0.004   # 0.4%

# =============================================================================


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Position:
    direction: str
    leverage:  float
    entries:   list = field(default_factory=list)  # [(execution_price, margin_usd), ...]
    extra_margin: float = 0.0                       # isolated margin (no notional increase)
    bullets_position_used: int = 0
    bullets_margin_used:   int = 0

    # -- Derived metrics -------------------------------------------------------

    @property
    def total_contracts(self) -> float:
        """Coins/contracts held (sum over all position entries)."""
        return sum(margin * self.leverage / price for price, margin in self.entries)

    @property
    def avg_entry_price(self) -> float:
        """Weighted average entry price (leverage cancels out)."""
        total_margin = sum(m for _, m in self.entries)
        total_inv    = sum(m / p for p, m in self.entries)
        return total_margin / total_inv

    @property
    def total_position_margin(self) -> float:
        return sum(m for _, m in self.entries)

    @property
    def total_effective_margin(self) -> float:
        return self.total_position_margin + self.extra_margin

    def roi_pct(self, current_price: float) -> float:
        """ROI on margin (includes leverage effect)."""
        contracts = self.total_contracts
        avg_entry = self.avg_entry_price
        if self.direction == "LONG":
            pnl = (current_price - avg_entry) * contracts
        else:
            pnl = (avg_entry - current_price) * contracts
        return pnl / self.total_position_margin * 100

    def unrealized_pnl(self, current_price: float) -> float:
        contracts = self.total_contracts
        avg_entry = self.avg_entry_price
        if self.direction == "LONG":
            return (current_price - avg_entry) * contracts
        else:
            return (avg_entry - current_price) * contracts

    def liquidation_price(self) -> float:
        """Simplified isolated-margin liquidation price."""
        notional     = self.total_contracts * self.avg_entry_price
        eff_rate     = self.total_effective_margin / notional
        avg          = self.avg_entry_price
        mmr          = MAINTENANCE_MARGIN_RATE
        if self.direction == "LONG":
            return avg * (1 - eff_rate + mmr)
        else:
            return avg * (1 + eff_rate - mmr)


@dataclass
class TradeRecord:
    trade_id:              int
    entry_time:            str
    exit_time:             str
    direction:             str
    avg_entry_price:       float
    exit_price:            float
    bullets_used_position: int
    bullets_used_margin:   int
    total_margin_usd:      float
    pnl_usd:               float
    roi_pct:               float
    exit_reason:           str   # TP | SL | LIQUIDATION | END_OF_PERIOD


@dataclass
class BulletLogRecord:
    timestamp:         str
    price:             float
    action:            str   # OPEN | DCA_POSITION | DCA_MARGIN | CLOSE
    bullets_count:     int
    pos_avg_entry:     float
    roi_pct:           float
    liquidation_price: float
    bullets_remaining: int


# ─── CSV helpers ──────────────────────────────────────────────────────────────

def load_klines(pair: str, interval: str, start: datetime, end: datetime) -> list[dict]:
    filename = Path(f"{pair}_{interval}.csv")
    if not filename.exists():
        raise FileNotFoundError(
            f"Data file '{filename}' not found. "
            f"Run fetch_klines.py with SYMBOL='{pair}' and INTERVAL='{interval}' first."
        )
    rows = []
    with open(filename, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if start <= dt <= end:
                rows.append({
                    "open_time": dt,
                    "open":  float(row["open"]),
                    "high":  float(row["high"]),
                    "low":   float(row["low"]),
                    "close": float(row["close"]),
                })
    return rows


def export_trades(records: list[TradeRecord]) -> None:
    fields = [
        "trade_id", "entry_time", "exit_time", "direction",
        "avg_entry_price", "exit_price", "bullets_used_position",
        "bullets_used_margin", "total_margin_usd", "pnl_usd",
        "roi_pct", "exit_reason",
    ]
    with open("trades_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({
                "trade_id":              r.trade_id,
                "entry_time":            r.entry_time,
                "exit_time":             r.exit_time,
                "direction":             r.direction,
                "avg_entry_price":       f"{r.avg_entry_price:.4f}",
                "exit_price":            f"{r.exit_price:.4f}",
                "bullets_used_position": r.bullets_used_position,
                "bullets_used_margin":   r.bullets_used_margin,
                "total_margin_usd":      f"{r.total_margin_usd:.2f}",
                "pnl_usd":               f"{r.pnl_usd:.2f}",
                "roi_pct":               f"{r.roi_pct:.2f}",
                "exit_reason":           r.exit_reason,
            })


def export_bullets(records: list[BulletLogRecord]) -> None:
    fields = [
        "timestamp", "price", "action", "bullets_count",
        "pos_avg_entry", "roi_pct", "liquidation_price", "bullets_remaining",
    ]
    with open("bullets_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({
                "timestamp":         r.timestamp,
                "price":             f"{r.price:.4f}",
                "action":            r.action,
                "bullets_count":     r.bullets_count,
                "pos_avg_entry":     f"{r.pos_avg_entry:.4f}",
                "roi_pct":           f"{r.roi_pct:.2f}",
                "liquidation_price": f"{r.liquidation_price:.4f}",
                "bullets_remaining": r.bullets_remaining,
            })


# ─── Simulation helpers ───────────────────────────────────────────────────────

def find_dca_tier(roi: float) -> tuple[int, int]:
    """Return (bullets_to_position, bullets_to_margin) for the given ROI %."""
    for lower_bound, pos_b, margin_b in DCA_TIERS:
        if roi >= lower_bound:
            return pos_b, margin_b
    return 0, 0


def dt_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def open_position(price: float, n_bullets: int, bullet_value: float,
                  direction: str, leverage: float) -> Position:
    pos = Position(direction=direction, leverage=leverage)
    pos.entries.append((price, bullet_value * n_bullets))
    pos.bullets_position_used = n_bullets
    return pos


def close_trade(pos: Position, exit_price: float, exit_time: datetime,
                entry_time: datetime, trade_id: int, exit_reason: str) -> TradeRecord:
    pnl = pos.unrealized_pnl(exit_price)
    roi = pos.roi_pct(exit_price)
    return TradeRecord(
        trade_id              = trade_id,
        entry_time            = dt_str(entry_time),
        exit_time             = dt_str(exit_time),
        direction             = pos.direction,
        avg_entry_price       = pos.avg_entry_price,
        exit_price            = exit_price,
        bullets_used_position = pos.bullets_position_used,
        bullets_used_margin   = pos.bullets_margin_used,
        total_margin_usd      = pos.total_effective_margin,
        pnl_usd               = pnl,
        roi_pct               = roi,
        exit_reason           = exit_reason,
    )


# ─── Main simulation ──────────────────────────────────────────────────────────

def run_backtest():
    # Parse dates
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.strptime(END_DATE,   "%Y-%m-%d").replace(tzinfo=timezone.utc) \
               + timedelta(days=1) - timedelta(seconds=1)

    print(f"Loading {PAIR}_{INTERVAL}.csv  [{START_DATE} → {END_DATE}] …")
    candles = load_klines(PAIR, INTERVAL, start_dt, end_dt)
    if not candles:
        print("No candles found for the given date range.")
        return

    running_capital   = TOTAL_CAPITAL        # tracks real equity across trade cycles
    bullet_value      = running_capital / TOTAL_BULLETS
    bullets_pos_rem   = TOTAL_BULLETS     # position bullets remaining
    bullets_mar_rem   = TOTAL_BULLETS     # margin  bullets remaining (same pool shown separately)

    pos: Position | None = None
    entry_time: datetime | None = None
    next_check  = candles[0]["open_time"]
    trade_id    = 0

    trades:      list[TradeRecord]    = []
    bullet_log:  list[BulletLogRecord] = []
    equity_curve: list[float]         = []     # total equity at each candle close
    peak_equity   = TOTAL_CAPITAL
    max_drawdown  = 0.0

    def _log_bullet(ts, price, action, count, p: Position):
        bullet_log.append(BulletLogRecord(
            timestamp         = dt_str(ts),
            price             = price,
            action            = action,
            bullets_count     = count,
            pos_avg_entry     = p.avg_entry_price,
            roi_pct           = p.roi_pct(price),
            liquidation_price = p.liquidation_price(),
            bullets_remaining = bullets_pos_rem,
        ))

    # Minimum capital to keep trading — one bullet must be worth at least $0.01
    MIN_CAPITAL = TOTAL_BULLETS * 0.01

    def _end_trade_cycle(pnl: float):
        """Update running capital after a trade closes and reset the bullet pool."""
        nonlocal running_capital, bullet_value, bullets_pos_rem, bullets_mar_rem
        running_capital += pnl
        if running_capital <= MIN_CAPITAL:
            running_capital = 0
            bullets_pos_rem = 0
            bullets_mar_rem = 0
            print("  *** BANKRUPT — no capital remaining, simulation stopped ***")
        else:
            bullets_pos_rem = TOTAL_BULLETS
            bullets_mar_rem = TOTAL_BULLETS
            bullet_value    = running_capital / TOTAL_BULLETS

    for candle in candles:
        ts    = candle["open_time"]
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]

        # ── 1. Liquidation check ─────────────────────────────────────────────
        if pos is not None:
            liq = pos.liquidation_price()
            triggered = (pos.direction == "LONG"  and l <= liq) or \
                        (pos.direction == "SHORT" and h >= liq)
            if triggered:
                liq_price = liq
                trade_id += 1
                t = close_trade(pos, liq_price, ts, entry_time, trade_id, "LIQUIDATION")
                trades.append(t)
                print(f"  [{dt_str(ts)}] LIQUIDATED at {liq_price:.2f}  "
                      f"PnL=${t.pnl_usd:+.2f}  (trade #{trade_id})")
                pos = None
                _end_trade_cycle(t.pnl_usd)
                # Don't open a new position mid-candle after liquidation;
                # the next bullet_check will open one.

        # ── 2. Bullet check ──────────────────────────────────────────────────
        if ts >= next_check:
            next_check += timedelta(hours=BULLET_INTERVAL_H)

            if pos is None and bullets_pos_rem > 0:
                # Open new position
                n = min(INITIAL_BULLETS, bullets_pos_rem)
                pos        = open_position(o, n, bullet_value, DIRECTION, LEVERAGE)
                entry_time = ts
                bullets_pos_rem -= n
                _log_bullet(ts, o, "OPEN", n, pos)
                print(f"  [{dt_str(ts)}] OPEN  {n} bullets @ {o:.2f}  "
                      f"avg={pos.avg_entry_price:.2f}  liq={pos.liquidation_price():.2f}")

            elif pos is not None:
                roi = pos.roi_pct(c)

                # Take profit
                if roi >= TP_PCT:
                    trade_id += 1
                    t = close_trade(pos, c, ts, entry_time, trade_id, "TP")
                    trades.append(t)
                    print(f"  [{dt_str(ts)}] TP    @ {c:.2f}  ROI={roi:+.1f}%  "
                          f"PnL=${t.pnl_usd:+.2f}  (trade #{trade_id})")
                    _end_trade_cycle(t.pnl_usd)
                    # Immediately open next position (if still solvent)
                    if bullets_pos_rem > 0:
                        n = min(INITIAL_BULLETS, bullets_pos_rem)
                        pos        = open_position(c, n, bullet_value, DIRECTION, LEVERAGE)
                        entry_time = ts
                        bullets_pos_rem -= n
                        _log_bullet(ts, c, "OPEN", n, pos)
                        print(f"  [{dt_str(ts)}] OPEN  {n} bullets @ {c:.2f}  "
                              f"avg={pos.avg_entry_price:.2f}  liq={pos.liquidation_price():.2f}")
                    else:
                        pos = None

                # Stop loss
                elif SL_PCT is not None and roi <= -SL_PCT:
                    trade_id += 1
                    t = close_trade(pos, c, ts, entry_time, trade_id, "SL")
                    trades.append(t)
                    print(f"  [{dt_str(ts)}] SL    @ {c:.2f}  ROI={roi:+.1f}%  "
                          f"PnL=${t.pnl_usd:+.2f}  (trade #{trade_id})")
                    pos = None
                    _end_trade_cycle(t.pnl_usd)

                # DCA
                elif bullets_pos_rem > 0 or bullets_mar_rem > 0:
                    tier_pos, tier_mar = find_dca_tier(roi)

                    # Add to position
                    if tier_pos > 0 and bullets_pos_rem > 0:
                        add_pos = min(tier_pos, bullets_pos_rem)
                        pos.entries.append((c, bullet_value * add_pos))
                        pos.bullets_position_used += add_pos
                        bullets_pos_rem -= add_pos
                        _log_bullet(ts, c, "DCA_POSITION", add_pos, pos)
                        print(f"  [{dt_str(ts)}] DCA   +{add_pos} pos bullets @ {c:.2f}  "
                              f"ROI={roi:+.1f}%  avg={pos.avg_entry_price:.2f}  "
                              f"liq={pos.liquidation_price():.2f}  rem={bullets_pos_rem}")

                    # Add isolated margin
                    if tier_mar > 0 and bullets_mar_rem > 0:
                        add_mar = min(tier_mar, bullets_mar_rem)
                        pos.extra_margin          += bullet_value * add_mar
                        pos.bullets_margin_used   += add_mar
                        bullets_mar_rem           -= add_mar
                        _log_bullet(ts, c, "DCA_MARGIN", add_mar, pos)
                        print(f"  [{dt_str(ts)}] DCA   +{add_mar} margin bullets  "
                              f"liq → {pos.liquidation_price():.2f}  rem={bullets_mar_rem}")

                else:
                    print(f"  [{dt_str(ts)}] WAIT  bullets exhausted  ROI={roi:+.1f}%")

        # ── 3. Equity tracking (for max drawdown) ────────────────────────────
        # running_capital already reflects all closed-trade PnLs; add unrealized PnL if open
        if pos is not None:
            equity = running_capital + pos.unrealized_pnl(c)
        else:
            equity = running_capital
        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        drawdown    = peak_equity - equity
        max_drawdown = max(max_drawdown, drawdown)

    # ── Close any remaining open position ────────────────────────────────────
    if pos is not None:
        last   = candles[-1]
        trade_id += 1
        t = close_trade(pos, last["close"], last["open_time"],
                        entry_time, trade_id, "END_OF_PERIOD")
        trades.append(t)
        _end_trade_cycle(t.pnl_usd)
        print(f"  [{dt_str(last['open_time'])}] END   closed @ {last['close']:.2f}  "
              f"ROI={t.roi_pct:+.1f}%  PnL=${t.pnl_usd:+.2f}  (trade #{trade_id})")

    # ── Print summary ─────────────────────────────────────────────────────────
    wins       = [t for t in trades if t.pnl_usd > 0]
    losses     = [t for t in trades if t.pnl_usd <= 0]
    total_pnl  = sum(t.pnl_usd for t in trades)
    total_ret  = total_pnl / TOTAL_CAPITAL * 100
    avg_roi    = sum(t.roi_pct for t in trades) / len(trades) if trades else 0
    liquidations = sum(1 for t in trades if t.exit_reason == "LIQUIDATION")
    win_rate   = len(wins) / len(trades) * 100 if trades else 0

    print()
    print("=" * 42)
    print("       BACKTEST RESULTS")
    print("=" * 42)
    print(f"  Period      : {START_DATE} → {END_DATE}")
    print(f"  Pair        : {PAIR} {INTERVAL}  |  {DIRECTION}  |  {LEVERAGE}x")
    print(f"  Capital     : ${TOTAL_CAPITAL:,.2f} → ${running_capital:,.2f}  "
          f"({TOTAL_BULLETS} bullets)")
    print("-" * 42)
    print(f"  Trades      : {len(trades)}  ({len(wins)} wins, {len(losses)} losses)")
    print(f"  Win rate    : {win_rate:.1f}%")
    print(f"  Total PnL   : ${total_pnl:+,.2f}  ({total_ret:+.1f}%)")
    print(f"  Max drawdown: ${max_drawdown:,.2f}  "
          f"({max_drawdown / TOTAL_CAPITAL * 100:.1f}%)")
    print(f"  Avg ROI/trade: {avg_roi:+.2f}%")
    print(f"  Liquidations: {liquidations}")
    print("=" * 42)

    # ── Export CSVs ───────────────────────────────────────────────────────────
    export_trades(trades)
    export_bullets(bullet_log)
    print(f"\n  trades_log.csv  → {len(trades)} rows")
    print(f"  bullets_log.csv → {len(bullet_log)} rows")


if __name__ == "__main__":
    run_backtest()
