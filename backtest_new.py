"""
Backtest: NEW settings vs OLD settings comparison
SL=1.2x ATR, TP=2.0x ATR, max 4h, no overnight
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone

RSI_PERIOD = 14
RSI_OVERSOLD = 65
ATR_PERIOD = 14

MARGIN_USD = 400
LEVERAGE = 20
EXPOSURE_USD = MARGIN_USD * LEVERAGE
SESSIONS = {"London": (7, 16), "US": (13, 22)}


def compute_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(high, low, close, period=ATR_PERIOD):
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def run_backtest(sl_mult, tp_mult, max_duration, close_before, label):
    """Run a backtest with given parameters."""
    trades = []
    in_trade = False
    entry_price = stop_loss = take_profit = 0
    entry_time = None
    direction = ""

    for i in range(50, len(df)):
        row = df.iloc[i]
        price = float(row["Close"])
        high = float(row["High"])
        low = float(row["Low"])
        rsi = float(row["RSI"])
        atr = float(row["ATR"])
        support = float(row["Support"])
        resistance = float(row["Resistance"])
        ema50 = float(row["EMA50"])
        hour_utc = row.name.hour

        in_session = any(s <= hour_utc < e for s, e in SESSIONS.values())
        too_late = hour_utc >= close_before

        if in_trade:
            duration = (row.name - entry_time).total_seconds() / 60
            troy_oz = EXPOSURE_USD / entry_price

            # Force close: overnight or max duration
            if hour_utc >= 21 or (max_duration and duration >= max_duration):
                if direction == "long":
                    pnl_pts = price - entry_price
                else:
                    pnl_pts = entry_price - price
                pnl_usd = round(pnl_pts * troy_oz, 2)
                outcome = "WIN" if pnl_pts > 0 else "LOSS"
                trades.append({"outcome": outcome, "pnl_usd": pnl_usd, "duration_min": duration, "reason": "FORCE"})
                in_trade = False
                continue

            if direction == "long":
                if high >= take_profit:
                    pnl_usd = round((take_profit - entry_price) * troy_oz, 2)
                    trades.append({"outcome": "WIN", "pnl_usd": pnl_usd, "duration_min": duration, "reason": "TP"})
                    in_trade = False
                elif low <= stop_loss:
                    pnl_usd = round((stop_loss - entry_price) * troy_oz, 2)
                    trades.append({"outcome": "LOSS", "pnl_usd": pnl_usd, "duration_min": duration, "reason": "SL"})
                    in_trade = False
            else:
                if low <= take_profit:
                    pnl_usd = round((entry_price - take_profit) * troy_oz, 2)
                    trades.append({"outcome": "WIN", "pnl_usd": pnl_usd, "duration_min": duration, "reason": "TP"})
                    in_trade = False
                elif high >= stop_loss:
                    pnl_usd = round((entry_price - stop_loss) * troy_oz, 2)
                    trades.append({"outcome": "LOSS", "pnl_usd": pnl_usd, "duration_min": duration, "reason": "SL"})
                    in_trade = False
        else:
            if pd.isna(rsi) or pd.isna(atr) or pd.isna(support):
                continue
            if not in_session or too_late:
                continue

            near_support = price <= support + (atr * 8)
            near_resistance = price >= resistance - (atr * 8)
            trend_up = price > ema50
            trend_down = price < ema50

            if trend_up and near_support and rsi < RSI_OVERSOLD:
                entry_price = price
                stop_loss = price - (atr * sl_mult)
                take_profit = price + (atr * tp_mult)
                entry_time = row.name
                direction = "long"
                in_trade = True
            elif trend_down and near_resistance and rsi > (100 - RSI_OVERSOLD):
                entry_price = price
                stop_loss = price + (atr * sl_mult)
                take_profit = price - (atr * tp_mult)
                entry_time = row.name
                direction = "short"
                in_trade = True

    # Print results
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    total = len(trades)
    if not total:
        print("  No trades!")
        return

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total_pnl = sum(t["pnl_usd"] for t in trades)
    avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0
    avg_dur = sum(t["duration_min"] for t in trades) / total
    force_closed = [t for t in trades if t["reason"] == "FORCE"]

    print(f"  Total trades   : {total}")
    print(f"  Wins           : {len(wins)}")
    print(f"  Losses         : {len(losses)}")
    print(f"  Win rate       : {len(wins)/total*100:.1f}%")
    print(f"  Total P&L      : ${total_pnl:.2f}")
    print(f"  Avg win        : ${avg_win:.2f}")
    print(f"  Avg loss       : ${avg_loss:.2f}")
    print(f"  Avg duration   : {avg_dur:.0f} min")
    print(f"  Force-closed   : {len(force_closed)} trades")
    if losses and sum(t["pnl_usd"] for t in losses) != 0:
        pf = abs(sum(t["pnl_usd"] for t in wins) / sum(t["pnl_usd"] for t in losses))
        print(f"  Profit factor  : {pf:.2f}")

    # Max drawdown
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
    print(f"  Max drawdown   : ${max_dd:.2f}")


# Fetch data once
print("Fetching 60 days of 1H gold data...")
df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
print(f"Got {len(df)} candles ({df.index[0]} to {df.index[-1]})")

df["RSI"] = compute_rsi(df["Close"])
df["ATR"] = compute_atr(df["High"], df["Low"], df["Close"])
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()

# Run both
run_backtest(0.75, 1.5, None, 21, "OLD: SL=0.75x ATR, TP=1.5x ATR, no max duration")
run_backtest(1.2, 2.0, 240, 19, "NEW: SL=1.2x ATR, TP=2.0x ATR, 4h max, close@19UTC")

print("\n\nDone.")
