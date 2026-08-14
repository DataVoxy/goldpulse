"""
Compare multiple strategy variants to find most robust settings.
Tests different SL/TP ratios, max durations, and entry filters.
"""
import yfinance as yf
import pandas as pd
from itertools import product

RSI_PERIOD = 14
ATR_PERIOD = 14
MARGIN_USD = 400
LEVERAGE = 20
EXPOSURE_USD = MARGIN_USD * LEVERAGE
SESSIONS = {"London": (7, 16), "US": (13, 22)}
SPREAD_PER_SIDE = 0.50
SLIPPAGE_PER_SIDE = 0.20


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


def run_variant(df, sl_mult, tp_mult, max_dur, rsi_thresh, close_before):
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
            costs = (SPREAD_PER_SIDE + SLIPPAGE_PER_SIDE) * troy_oz * 2

            if hour_utc >= 21 or (max_dur and duration >= max_dur):
                if direction == "long":
                    pnl_pts = price - entry_price
                else:
                    pnl_pts = entry_price - price
                pnl_usd = round(pnl_pts * troy_oz - costs, 2)
                trades.append(pnl_usd)
                in_trade = False
                continue

            if direction == "long":
                if high >= take_profit:
                    trades.append(round((take_profit - entry_price) * troy_oz - costs, 2))
                    in_trade = False
                elif low <= stop_loss:
                    trades.append(round((stop_loss - entry_price) * troy_oz - costs, 2))
                    in_trade = False
            else:
                if low <= take_profit:
                    trades.append(round((entry_price - take_profit) * troy_oz - costs, 2))
                    in_trade = False
                elif high >= stop_loss:
                    trades.append(round((entry_price - stop_loss) * troy_oz - costs, 2))
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

            if trend_up and near_support and rsi < rsi_thresh:
                entry_price = price
                stop_loss = price - (atr * sl_mult)
                take_profit = price + (atr * tp_mult)
                entry_time = row.name
                direction = "long"
                in_trade = True
            elif trend_down and near_resistance and rsi > (100 - rsi_thresh):
                entry_price = price
                stop_loss = price + (atr * sl_mult)
                take_profit = price - (atr * tp_mult)
                entry_time = row.name
                direction = "short"
                in_trade = True

    return trades


# Fetch data
print("Fetching data...")
df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
print(f"Got {len(df)} candles\n")

df["RSI"] = compute_rsi(df["Close"])
df["ATR"] = compute_atr(df["High"], df["Low"], df["Close"])
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()

# Test combinations
sl_mults = [1.0, 1.2, 1.5, 2.0]
tp_mults = [2.0, 2.5, 3.0]
max_durs = [180, 240, 360]
rsi_thresholds = [60, 65, 70]
close_befores = [18, 19]

results = []

for sl, tp, dur, rsi_t, cb in product(sl_mults, tp_mults, max_durs, rsi_thresholds, close_befores):
    trades = run_variant(df, sl, tp, dur, rsi_t, cb)
    if len(trades) < 10:  # skip if too few trades
        continue
    total_pnl = sum(trades)
    wins = [t for t in trades if t > 0]
    wr = len(wins) / len(trades) * 100
    avg_per_trade = total_pnl / len(trades)
    max_dd = 0
    equity = 0
    peak = 0
    for t in trades:
        equity += t
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    results.append({
        "sl": sl, "tp": tp, "dur": dur, "rsi": rsi_t, "cb": cb,
        "trades": len(trades), "wr": wr, "pnl": total_pnl,
        "per_trade": avg_per_trade, "max_dd": max_dd
    })

# Sort by P&L
results.sort(key=lambda x: x["pnl"], reverse=True)

print(f"{'SL':>4} {'TP':>4} {'Dur':>4} {'RSI':>4} {'CB':>3} | {'#':>4} {'WR%':>5} {'P&L':>8} {'$/trade':>8} {'MaxDD':>7}")
print("-" * 75)
for r in results[:15]:
    print(f"{r['sl']:>4.1f} {r['tp']:>4.1f} {r['dur']:>4} {r['rsi']:>4} {r['cb']:>3} | {r['trades']:>4} {r['wr']:>5.1f} ${r['pnl']:>7.0f} ${r['per_trade']:>7.2f} ${r['max_dd']:>6.0f}")

print("\n--- CURRENT SETTINGS ---")
current = [r for r in results if r["sl"] == 1.2 and r["tp"] == 2.0 and r["dur"] == 240 and r["rsi"] == 65 and r["cb"] == 19]
if current:
    r = current[0]
    print(f"  SL={r['sl']} TP={r['tp']} Dur={r['dur']} RSI={r['rsi']} CB={r['cb']}")
    print(f"  Trades: {r['trades']} | WR: {r['wr']:.1f}% | P&L: ${r['pnl']:.0f} | $/trade: ${r['per_trade']:.2f} | MaxDD: ${r['max_dd']:.0f}")

print("\n--- TOP 3 (most profitable) ---")
for i, r in enumerate(results[:3], 1):
    print(f"  #{i}: SL={r['sl']} TP={r['tp']} Dur={r['dur']} RSI={r['rsi']} CB={r['cb']}")
    print(f"      Trades: {r['trades']} | WR: {r['wr']:.1f}% | P&L: ${r['pnl']:.0f} | $/trade: ${r['per_trade']:.2f} | MaxDD: ${r['max_dd']:.0f}")

# Best risk-adjusted (highest P&L / MaxDD ratio)
for r in results:
    r["risk_adj"] = r["pnl"] / r["max_dd"] if r["max_dd"] > 0 else 0
results_ra = sorted(results, key=lambda x: x["risk_adj"], reverse=True)

print("\n--- TOP 3 (best risk-adjusted: P&L / MaxDD) ---")
for i, r in enumerate(results_ra[:3], 1):
    print(f"  #{i}: SL={r['sl']} TP={r['tp']} Dur={r['dur']} RSI={r['rsi']} CB={r['cb']}")
    print(f"      Trades: {r['trades']} | WR: {r['wr']:.1f}% | P&L: ${r['pnl']:.0f} | $/trade: ${r['per_trade']:.2f} | MaxDD: ${r['max_dd']:.0f} | Ratio: {r['risk_adj']:.2f}")
