"""Backtest with trend filter + sell signals."""
import yfinance as yf
import pandas as pd

df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

print(f"Data: {len(df)} candles")

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1, min_periods=p).mean() / l.ewm(com=p-1, min_periods=p).mean()))

def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"])
df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["EMA200"] = df["Close"].ewm(span=200).mean()

SESSIONS = {"London": (7, 16), "US": (13, 22)}

configs = [
    # (label, sl_mult, tp_mult, buy_only, trend_filter, rsi_threshold)
    ("Current (no filter)", 1.5, 0.5, True, False, 65),
    ("+ Trend filter", 1.5, 0.5, True, True, 65),
    ("+ Trend + 1:2 ratio", 0.75, 1.5, True, True, 65),
    ("+ Trend + 1:3 ratio", 0.5, 1.5, True, True, 65),
    ("Buy+Sell no filter", 1.5, 0.5, False, False, 65),
    ("Buy+Sell + trend", 0.75, 1.5, False, True, 65),
    ("Buy+Sell + trend RSI50", 0.75, 1.5, False, True, 50),
    ("BEST: Sell only + trend", 0.75, 1.5, "sell_only", True, 35),
]

print("=" * 90)
print(f"{'Strategy':<25} {'Trades':>6} {'WR':>6} {'Avg W':>8} {'Avg L':>9} {'P&L':>10} {'MaxDD':>8}")
print("-" * 90)

for label, sl_mult, tp_mult, buy_only, trend_filter, rsi_thresh in configs:
    trades = []
    in_trade = False
    direction = None  # "long" or "short"
    entry_price = sl = tp = 0
    equity = 0
    peak = 0
    max_dd = 0

    for i in range(200, len(df)):
        row = df.iloc[i]
        price = float(row["Close"])
        high = float(row["High"])
        low = float(row["Low"])
        r = float(row["RSI"])
        a = float(row["ATR"])
        sup = float(row["Support"])
        res = float(row["Resistance"])
        ema50 = float(row["EMA50"])
        ema200 = float(row["EMA200"])
        hour = row.name.hour
        in_session = any(s <= hour < e for s, e in SESSIONS.values())

        if pd.isna(r) or pd.isna(a) or pd.isna(sup) or pd.isna(ema50):
            continue

        if in_trade:
            if direction == "long":
                if high >= tp:
                    pnl = tp - entry_price
                    trades.append(("WIN", pnl))
                    equity += pnl * (8000/4100)
                    in_trade = False
                elif low <= sl:
                    pnl = sl - entry_price
                    trades.append(("LOSS", pnl))
                    equity += pnl * (8000/4100)
                    in_trade = False
            elif direction == "short":
                if low <= tp:  # TP for short is below entry
                    pnl = entry_price - tp
                    trades.append(("WIN", pnl))
                    equity += pnl * (8000/4100)
                    in_trade = False
                elif high >= sl:  # SL for short is above entry
                    pnl = entry_price - sl
                    trades.append(("LOSS", pnl))
                    equity += pnl * (8000/4100)
                    in_trade = False

            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        else:
            too_late = hour >= 21

            # LONG entry
            if buy_only is True or buy_only is False:
                near_support = price <= sup + (a * 5)
                rsi_ok = r < rsi_thresh
                trend_ok = (price > ema50) if trend_filter else True

                if in_session and near_support and rsi_ok and trend_ok and not too_late:
                    if buy_only is not "sell_only":
                        entry_price = price
                        sl = price - (a * sl_mult)
                        tp = price + (a * tp_mult)
                        direction = "long"
                        in_trade = True
                        continue

            # SHORT entry
            if buy_only is False or buy_only == "sell_only":
                near_resistance = price >= res - (a * 5)
                rsi_overbought = r > (100 - rsi_thresh)  # mirror of oversold
                trend_down = (price < ema50) if trend_filter else True

                if in_session and near_resistance and rsi_overbought and trend_down and not too_late:
                    entry_price = price
                    sl = price + (a * sl_mult)  # SL above for short
                    tp = price - (a * tp_mult)  # TP below for short
                    direction = "short"
                    in_trade = True

    wins = [t for t in trades if t[0] == "WIN"]
    losses = [t for t in trades if t[0] == "LOSS"]
    total = len(trades)
    if total == 0:
        print(f"{label:<25} {'0 trades'}")
        continue

    wr = len(wins) / total * 100
    toz = 8000 / 4100
    avg_w = sum(t[1] * toz for t in wins) / len(wins) if wins else 0
    avg_l = sum(t[1] * toz for t in losses) / len(losses) if losses else 0
    total_pnl = sum(t[1] * toz for t in trades)

    marker = " <<<" if total_pnl > 0 else ""
    print(f"{label:<25} {total:>6} {wr:>5.1f}% ${avg_w:>7.2f} ${avg_l:>8.2f} ${total_pnl:>9.2f} ${max_dd:>7.2f}{marker}")

print("=" * 90)
print("\n<<< = PROFITABLE strategy")
