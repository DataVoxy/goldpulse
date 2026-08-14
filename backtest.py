"""
GoldPulse Backtest
------------------
Simulates the current strategy against 60 days of 1H gold data.
Shows what would have happened if the bot ran perfectly over that period.

Usage:
  py backtest.py
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timezone

# ==========================
# STRATEGY PARAMETERS (match strategy.py)
# ==========================
RSI_PERIOD = 14
RSI_OVERSOLD = 65          # Current live setting
ATR_PERIOD = 14
SL_ATR_MULT = 0.75
TP_ATR_MULT = 1.5
MARGIN_USD = 400
LEVERAGE = 20
EXPOSURE_USD = MARGIN_USD * LEVERAGE

# Sessions (UTC hours)
SESSIONS = {
    "London": (7, 16),
    "US": (13, 22),
}
CLOSE_BEFORE_HOUR = 21

# ==========================
# INDICATORS
# ==========================
def compute_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_atr(high, low, close, period=ATR_PERIOD):
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

# ==========================
# FETCH DATA
# ==========================
print("Fetching 60 days of 1H gold data...")
df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

if df.empty or len(df) < 50:
    print("ERROR: Not enough data")
    exit(1)

print(f"Got {len(df)} candles from {df.index[0]} to {df.index[-1]}")

# ==========================
# COMPUTE INDICATORS
# ==========================
df["RSI"] = compute_rsi(df["Close"])
df["ATR"] = compute_atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()

# ==========================
# SIMULATE TRADES
# ==========================
trades = []
in_trade = False
entry_price = 0
stop_loss = 0
take_profit = 0
entry_time = None
entry_rsi = 0
entry_atr = 0

for i in range(50, len(df)):
    row = df.iloc[i]
    price = float(row["Close"])
    high = float(row["High"])
    low = float(row["Low"])
    rsi = float(row["RSI"])
    atr = float(row["ATR"])
    support = float(row["Support"])
    
    # Get hour for session check
    hour_utc = row.name.hour if hasattr(row.name, 'hour') else 12
    
    # Check if in active session
    in_session = False
    for name, (start, end) in SESSIONS.items():
        if start <= hour_utc < end:
            in_session = True
            break
    
    too_late = hour_utc >= CLOSE_BEFORE_HOUR
    
    if in_trade:
        # Check if TP or SL hit using high/low of candle
        if high >= take_profit:
            # TP hit
            pnl_pts = take_profit - entry_price
            troy_oz = EXPOSURE_USD / entry_price
            pnl_usd = round(pnl_pts * troy_oz, 2)
            duration = (row.name - entry_time).total_seconds() / 60
            trades.append({
                "entry_time": entry_time,
                "exit_time": row.name,
                "entry": round(entry_price, 2),
                "exit": round(take_profit, 2),
                "outcome": "WIN",
                "pnl_pts": round(pnl_pts, 2),
                "pnl_usd": pnl_usd,
                "rsi": round(entry_rsi, 1),
                "atr": round(entry_atr, 2),
                "duration_min": round(duration, 1),
            })
            in_trade = False
            
        elif low <= stop_loss:
            # SL hit
            pnl_pts = stop_loss - entry_price
            troy_oz = EXPOSURE_USD / entry_price
            pnl_usd = round(pnl_pts * troy_oz, 2)
            duration = (row.name - entry_time).total_seconds() / 60
            trades.append({
                "entry_time": entry_time,
                "exit_time": row.name,
                "entry": round(entry_price, 2),
                "exit": round(stop_loss, 2),
                "outcome": "LOSS",
                "pnl_pts": round(pnl_pts, 2),
                "pnl_usd": pnl_usd,
                "rsi": round(entry_rsi, 1),
                "atr": round(entry_atr, 2),
                "duration_min": round(duration, 1),
            })
            in_trade = False
    
    else:
        # Check entry conditions
        if pd.isna(rsi) or pd.isna(atr) or pd.isna(support):
            continue
            
        near_support = price <= support + (atr * 5)
        rsi_oversold = rsi < RSI_OVERSOLD
        
        if in_session and near_support and rsi_oversold and not too_late:
            # ENTER LONG
            entry_price = price
            stop_loss = price - (atr * SL_ATR_MULT)
            take_profit = price + (atr * TP_ATR_MULT)
            entry_time = row.name
            entry_rsi = rsi
            entry_atr = atr
            in_trade = True

# ==========================
# RESULTS
# ==========================
print("\n" + "=" * 60)
print("BACKTEST RESULTS — GoldPulse Strategy (60 days, 1H)")
print("=" * 60)

if not trades:
    print("No trades generated!")
    exit(0)

wins = [t for t in trades if t["outcome"] == "WIN"]
losses = [t for t in trades if t["outcome"] == "LOSS"]
total = len(trades)
win_rate = len(wins) / total * 100
total_pnl = sum(t["pnl_usd"] for t in trades)
avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0
avg_duration = sum(t["duration_min"] for t in trades) / total

print(f"\nTotal trades   : {total}")
print(f"Wins           : {len(wins)}")
print(f"Losses         : {len(losses)}")
print(f"Win rate       : {win_rate:.1f}%")
print(f"")
print(f"Total P&L      : ${total_pnl:.2f}")
print(f"Avg win        : ${avg_win:.2f}")
print(f"Avg loss       : ${avg_loss:.2f}")
print(f"Avg duration   : {avg_duration:.0f} min")
print(f"")
print(f"Profit factor  : {abs(sum(t['pnl_usd'] for t in wins) / sum(t['pnl_usd'] for t in losses)):.2f}" if losses else "N/A (no losses)")

# Break-even analysis
if losses:
    needed_wr = abs(avg_loss) / (abs(avg_loss) + avg_win) * 100
    print(f"Break-even WR  : {needed_wr:.1f}%")
    print(f"Your WR vs BE  : {'+' if win_rate > needed_wr else ''}{win_rate - needed_wr:.1f}%")

# Best/worst
best = max(trades, key=lambda t: t["pnl_usd"])
worst = min(trades, key=lambda t: t["pnl_usd"])
print(f"\nBest trade     : ${best['pnl_usd']} ({best['entry_time']})")
print(f"Worst trade    : ${worst['pnl_usd']} ({worst['entry_time']})")

# Win rate by RSI
print("\n--- Win Rate by RSI at Entry ---")
rsi_bins = [(0, 40), (40, 50), (50, 55), (55, 60), (60, 65)]
for low_r, high_r in rsi_bins:
    bin_trades = [t for t in trades if low_r <= t["rsi"] < high_r]
    if bin_trades:
        bin_wins = [t for t in bin_trades if t["outcome"] == "WIN"]
        print(f"  RSI {low_r}-{high_r}: {len(bin_trades)} trades, {len(bin_wins)/len(bin_trades)*100:.0f}% WR")

# Win rate by hour
print("\n--- Win Rate by Hour (UTC) ---")
for hour in range(7, 22):
    hour_trades = [t for t in trades if t["entry_time"].hour == hour]
    if hour_trades:
        hour_wins = [t for t in hour_trades if t["outcome"] == "WIN"]
        print(f"  {hour:02d}:00: {len(hour_trades)} trades, {len(hour_wins)/len(hour_trades)*100:.0f}% WR")

# Equity curve
print("\n--- Equity Curve ---")
equity = 0
peak = 0
max_dd = 0
for t in trades:
    equity += t["pnl_usd"]
    if equity > peak:
        peak = equity
    dd = peak - equity
    if dd > max_dd:
        max_dd = dd

print(f"  Final equity : ${equity:.2f}")
print(f"  Peak equity  : ${peak:.2f}")
print(f"  Max drawdown : ${max_dd:.2f}")

# Show last 10 trades
print("\n--- Last 10 Trades ---")
print(f"{'Entry Time':<22} {'Entry':>8} {'Exit':>8} {'Result':>6} {'P&L':>8} {'RSI':>5} {'Dur':>6}")
print("-" * 70)
for t in trades[-10:]:
    print(f"{str(t['entry_time']):<22} ${t['entry']:>7} ${t['exit']:>7} {t['outcome']:>6} ${t['pnl_usd']:>7} {t['rsi']:>5} {t['duration_min']:>5}m")

print("\n" + "=" * 60)
print("Done. This is a simulation — past performance does not guarantee future results.")
print("=" * 60)
