"""
Backtest the new ratio (SL=0.75, TP=1.5) over the last 7 days of 1H data.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# Settings (new ratio)
SL_ATR_MULT = 0.75
TP_ATR_MULT = 1.5
RSI_OVERSOLD = 65
ATR_PERIOD = 14
MARGIN_USD = 400
LEVERAGE = 20
EXPOSURE_USD = MARGIN_USD * LEVERAGE

SESSIONS = {"London": (7, 16), "US": (13, 22)}

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_atr(high, low, close, period=14):
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

# Fetch data
print("Fetching 14 days of 1H gold data (need extra for indicators)...")
df = yf.download("GC=F", period="14d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

if df.empty or len(df) < 50:
    print("ERROR: Not enough data")
    exit(1)

print(f"Got {len(df)} candles from {df.index[0]} to {df.index[-1]}")

# Compute indicators
df["RSI"] = compute_rsi(df["Close"])
df["ATR"] = compute_atr(df["High"], df["Low"], df["Close"])
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()

# Only look at last 7 days for trades
cutoff = df.index[-1] - timedelta(days=7)
start_idx = df.index.searchsorted(cutoff)
if start_idx < 50:
    start_idx = 50

print(f"Backtesting from {df.index[start_idx]} to {df.index[-1]} (last 7 days)")
print(f"Using: SL={SL_ATR_MULT}x ATR, TP={TP_ATR_MULT}x ATR (1:2 ratio)\n")

# Simulate
trades = []
in_trade = False
trade_dir = None
entry_price = sl = tp = 0
entry_time = None

for i in range(start_idx, len(df)):
    row = df.iloc[i]
    price = float(row["Close"])
    high = float(row["High"])
    low = float(row["Low"])
    rsi = float(row["RSI"])
    atr = float(row["ATR"])
    support = float(row["Support"])
    resistance = float(row["Resistance"])
    ema50 = float(row["EMA50"])

    hour_utc = row.name.hour if hasattr(row.name, 'hour') else 12
    in_session = any(s <= hour_utc < e for s, e in SESSIONS.values())
    too_late = hour_utc >= 21

    if pd.isna(rsi) or pd.isna(atr) or pd.isna(support):
        continue

    if in_trade:
        if trade_dir == "long":
            if high >= tp:
                pnl_pts = tp - entry_price
                troy_oz = EXPOSURE_USD / entry_price
                duration = (row.name - entry_time).total_seconds() / 60
                trades.append({"dir": "LONG", "entry": entry_price, "exit": tp, "outcome": "WIN", 
                             "pnl_pts": pnl_pts, "pnl_usd": round(pnl_pts * troy_oz, 2),
                             "time": entry_time, "duration": duration})
                in_trade = False
            elif low <= sl:
                pnl_pts = sl - entry_price
                troy_oz = EXPOSURE_USD / entry_price
                duration = (row.name - entry_time).total_seconds() / 60
                trades.append({"dir": "LONG", "entry": entry_price, "exit": sl, "outcome": "LOSS",
                             "pnl_pts": pnl_pts, "pnl_usd": round(pnl_pts * troy_oz, 2),
                             "time": entry_time, "duration": duration})
                in_trade = False
        elif trade_dir == "short":
            if low <= tp:
                pnl_pts = entry_price - tp
                troy_oz = EXPOSURE_USD / entry_price
                duration = (row.name - entry_time).total_seconds() / 60
                trades.append({"dir": "SHORT", "entry": entry_price, "exit": tp, "outcome": "WIN",
                             "pnl_pts": pnl_pts, "pnl_usd": round(pnl_pts * troy_oz, 2),
                             "time": entry_time, "duration": duration})
                in_trade = False
            elif high >= sl:
                pnl_pts = entry_price - sl
                troy_oz = EXPOSURE_USD / entry_price
                duration = (row.name - entry_time).total_seconds() / 60
                trades.append({"dir": "SHORT", "entry": entry_price, "exit": sl, "outcome": "LOSS",
                             "pnl_pts": pnl_pts, "pnl_usd": round(pnl_pts * troy_oz, 2),
                             "time": entry_time, "duration": duration})
                in_trade = False
    else:
        if not in_session or too_late:
            continue

        near_support = price <= support + (atr * 5)
        near_resistance = price >= resistance - (atr * 5)
        trend_up = price > ema50
        trend_down = price < ema50

        # Long entry
        if trend_up and near_support and rsi < RSI_OVERSOLD:
            entry_price = price
            sl = price - (atr * SL_ATR_MULT)
            tp = price + (atr * TP_ATR_MULT)
            in_trade = True
            trade_dir = "long"
            entry_time = row.name
        # Short entry
        elif trend_down and near_resistance and rsi > (100 - RSI_OVERSOLD):
            entry_price = price
            sl = price + (atr * SL_ATR_MULT)
            tp = price - (atr * TP_ATR_MULT)
            in_trade = True
            trade_dir = "short"
            entry_time = row.name

# Results
print(f"{'#':<3} {'Time':<20} {'Dir':<6} {'Entry':>8} {'Exit':>8} {'Result':>7} {'P&L':>8} {'Duration':>10}")
print("-" * 80)

for i, t in enumerate(trades, 1):
    dur_str = f"{t['duration']:.0f}m" if t['duration'] < 60 else f"{t['duration']/60:.1f}h"
    print(f"{i:<3} {str(t['time'])[:19]:<20} {t['dir']:<6} ${t['entry']:>7.2f} ${t['exit']:>7.2f} {t['outcome']:>7} ${t['pnl_usd']:>7.2f} {dur_str:>10}")

print(f"\n{'='*80}")
wins = [t for t in trades if t["outcome"] == "WIN"]
losses = [t for t in trades if t["outcome"] == "LOSS"]
total = len(trades)

if total == 0:
    print("No trades in this period.")
    exit(0)

win_rate = len(wins) / total * 100
total_pnl = sum(t["pnl_usd"] for t in trades)
avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0

print(f"LAST 7 DAYS — NEW RATIO (SL=0.75x, TP=1.5x ATR)")
print(f"{'='*80}")
print(f"Trades:     {total}")
print(f"Wins:       {len(wins)}")
print(f"Losses:     {len(losses)}")
print(f"Win rate:   {win_rate:.1f}%")
print(f"Total P&L:  ${total_pnl:.2f}")
print(f"Avg win:    ${avg_win:.2f}")
print(f"Avg loss:   ${avg_loss:.2f}")

if losses:
    pf = abs(sum(t["pnl_usd"] for t in wins)) / abs(sum(t["pnl_usd"] for t in losses))
    print(f"Profit fac: {pf:.2f}")

# Equity curve
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
print(f"Max DD:     ${max_dd:.2f}")
print(f"{'='*80}")
