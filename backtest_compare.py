"""
Backtest comparison: 3 trade management modes
1. No time limit (trades only close on TP/SL)
2. Hard 4-hour force-close
3. Breakeven stop after 4h (new logic)

Uses same entry conditions as the live strategy.
"""
import yfinance as yf
import pandas as pd

# Download data
print("Downloading 6 months of gold data...")
df = yf.download("GC=F", period="6mo", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
print(f"Data: {len(df)} candles | {df.index[0].date()} to {df.index[-1].date()}\n")

# Indicators
def rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1, min_periods=p).mean() / l.ewm(com=p-1, min_periods=p).mean()))

def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"])
df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()
df["EMA50"] = df["Close"].ewm(span=50).mean()

SESSIONS = {"London": (7, 16), "US": (13, 22)}
EXPOSURE = 8000  # $400 x 20 leverage
MAX_DURATION_BARS = 4  # 4 hours = 4 bars on 1H


def run_backtest(mode="no_limit"):
    """
    Modes:
    - 'no_limit': trades close only on TP/SL
    - 'force_close': close after 4h regardless
    - 'breakeven': move SL to entry after 4h if in profit, close if in loss
    """
    trades = []
    in_trade = False
    entry_price = sl = tp = 0
    direction = None
    entry_bar = 0
    sl_moved_to_be = False

    for i in range(200, len(df)):
        row = df.iloc[i]
        p = float(row["Close"])
        h = float(row["High"])
        lo = float(row["Low"])
        r = float(row["RSI"])
        a = float(row["ATR"])
        sup = float(row["Support"])
        res = float(row["Resistance"])
        ema = float(row["EMA50"])
        if pd.isna(r) or pd.isna(a) or pd.isna(ema):
            continue
        hr = row.name.hour
        in_session = any(s <= hr < e for s, e in SESSIONS.values())

        if in_trade:
            bars_held = i - entry_bar
            
            # Time-based management
            if bars_held >= MAX_DURATION_BARS:
                if mode == "force_close":
                    # Hard close at current price
                    pnl = _calc_pnl(direction, entry_price, p)
                    outcome = "WIN" if pnl > 0 else "LOSS"
                    trades.append((outcome, pnl, row.name, bars_held))
                    in_trade = False
                    continue
                elif mode == "breakeven" and not sl_moved_to_be:
                    # Check if in profit
                    if direction == "long" and p > entry_price:
                        sl = entry_price  # move SL to breakeven
                        sl_moved_to_be = True
                    elif direction == "short" and p < entry_price:
                        sl = entry_price
                        sl_moved_to_be = True
                    else:
                        # In loss after 4h → close
                        pnl = _calc_pnl(direction, entry_price, p)
                        trades.append(("LOSS", pnl, row.name, bars_held))
                        in_trade = False
                        continue

            # Normal TP/SL check (using potentially updated SL)
            if direction == "long":
                if h >= tp:
                    pnl = (tp - entry_price) * (EXPOSURE / entry_price)
                    trades.append(("WIN", pnl, row.name, bars_held))
                    in_trade = False
                elif lo <= sl:
                    pnl = (sl - entry_price) * (EXPOSURE / entry_price)
                    outcome = "WIN" if pnl >= 0 else "LOSS"
                    trades.append((outcome, pnl, row.name, bars_held))
                    in_trade = False
            else:  # short
                if lo <= tp:
                    pnl = (entry_price - tp) * (EXPOSURE / entry_price)
                    trades.append(("WIN", pnl, row.name, bars_held))
                    in_trade = False
                elif h >= sl:
                    pnl = (entry_price - sl) * (EXPOSURE / entry_price)
                    outcome = "WIN" if pnl >= 0 else "LOSS"
                    trades.append((outcome, pnl, row.name, bars_held))
                    in_trade = False
        else:
            # Entry conditions
            if not in_session or hr >= 21:
                continue
            # LONG: above EMA50 + near support + RSI < 65
            if p > ema and p <= sup + (a * 8) and r < 65:
                entry_price = p
                sl = p - (a * 1.2)
                tp = p + (a * 2.0)
                direction = "long"
                in_trade = True
                entry_bar = i
                sl_moved_to_be = False
            # SHORT: below EMA50 + near resistance + RSI > 35
            elif p < ema and p >= res - (a * 8) and r > 35:
                entry_price = p
                sl = p + (a * 1.2)
                tp = p - (a * 2.0)
                direction = "short"
                in_trade = True
                entry_bar = i
                sl_moved_to_be = False

    return trades


def _calc_pnl(direction, entry, exit_price):
    if direction == "long":
        return (exit_price - entry) * (EXPOSURE / entry)
    else:
        return (entry - exit_price) * (EXPOSURE / entry)


def print_results(name, trades):
    if not trades:
        print(f"  {name}: No trades")
        return

    wins = [t for t in trades if t[0] == "WIN"]
    losses = [t for t in trades if t[0] == "LOSS"]
    tot = len(trades)
    wr = len(wins) / tot * 100
    total_pnl = sum(t[1] for t in trades)
    avg_w = sum(t[1] for t in wins) / len(wins) if wins else 0
    avg_l = sum(t[1] for t in losses) / len(losses) if losses else 0
    avg_dur = sum(t[3] for t in trades) / tot

    # Equity curve for drawdown
    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        equity += t[1]
        if equity > peak:
            peak = equity
        if peak - equity > max_dd:
            max_dd = peak - equity

    # Remove top 3 trades to check robustness
    sorted_by_pnl = sorted(trades, key=lambda t: t[1], reverse=True)
    pnl_without_top3 = sum(t[1] for t in sorted_by_pnl[3:])

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Trades        : {tot}")
    print(f"  Wins/Losses   : {len(wins)}/{len(losses)}")
    print(f"  Win rate      : {wr:.1f}%")
    print(f"  Total P&L     : ${total_pnl:.2f}")
    print(f"  Per month     : ${total_pnl/6:.2f}")
    print(f"  Avg win       : ${avg_w:.2f}")
    print(f"  Avg loss      : ${avg_l:.2f}")
    print(f"  Avg duration  : {avg_dur:.1f} bars ({avg_dur:.1f}h)")
    print(f"  Max drawdown  : ${max_dd:.2f}")
    print(f"  P&L without top 3 wins: ${pnl_without_top3:.2f}")
    print(f"  {'ROBUST ✓' if pnl_without_top3 > 0 else 'NOT ROBUST ✗ (depends on outliers)'}")


# Run all three
print("Running backtests...\n")

t1 = run_backtest("no_limit")
t2 = run_backtest("force_close")
t3 = run_backtest("breakeven")

print_results("MODE 1: No time limit (TP/SL only)", t1)
print_results("MODE 2: Hard force-close after 4h", t2)
print_results("MODE 3: Breakeven stop after 4h (NEW)", t3)

print(f"\n{'='*60}")
print("CONCLUSION")
print(f"{'='*60}")
pnls = {
    "No limit": sum(t[1] for t in t1),
    "Force close 4h": sum(t[1] for t in t2),
    "Breakeven 4h": sum(t[1] for t in t3),
}
best = max(pnls, key=pnls.get)
print(f"  Best mode: {best} (${pnls[best]:.2f})")
for name, pnl in sorted(pnls.items(), key=lambda x: x[1], reverse=True):
    print(f"    {name:20s}: ${pnl:>8.2f}")
