"""Compare different SL:TP ratios on 60 days of data."""
import yfinance as yf
import pandas as pd

df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1,min_periods=p).mean() / l.ewm(com=p-1,min_periods=p).mean()))

def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"])
df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()

SESSIONS = {"London": (7,16), "US": (13,22)}

print("=" * 80)
print("RATIO COMPARISON — Same entries, different SL:TP")
print("=" * 80)
print(f"{'Ratio':<18} {'Trades':>6} {'WR':>7} {'Avg Win':>9} {'Avg Loss':>10} {'Total P&L':>11} {'Verdict':>10}")
print("-" * 80)

for sl_mult, tp_mult, label in [
    (1.5, 0.5, "Current 3:1 SL>TP"),
    (1.0, 1.0, "1:1 Equal"),
    (0.75, 1.5, "1:2 TP>SL"),
    (0.5, 1.5, "1:3 TP>SL"),
    (1.0, 2.0, "1:2 Wide TP"),
    (0.5, 2.0, "1:4 TP>SL"),
]:
    trades = []
    in_trade = False
    entry_price = sl = tp = 0
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        price = float(row["Close"])
        high = float(row["High"])
        low = float(row["Low"])
        r = float(row["RSI"])
        a = float(row["ATR"])
        sup = float(row["Support"])
        hour = row.name.hour
        in_session = any(s <= hour < e for s, e in SESSIONS.values())
        
        if in_trade:
            if high >= tp:
                trades.append(("WIN", tp - entry_price))
                in_trade = False
            elif low <= sl:
                trades.append(("LOSS", sl - entry_price))
                in_trade = False
        else:
            if pd.isna(r) or pd.isna(a) or pd.isna(sup):
                continue
            if in_session and price <= sup + (a*5) and r < 65 and hour < 21:
                entry_price = price
                sl = price - (a * sl_mult)
                tp = price + (a * tp_mult)
                in_trade = True
    
    wins = [t for t in trades if t[0] == "WIN"]
    losses = [t for t in trades if t[0] == "LOSS"]
    total = len(trades)
    if total == 0:
        continue
    
    wr = len(wins) / total * 100
    troy_oz = 8000 / 4100  # approximate
    avg_w = sum(t[1] * troy_oz for t in wins) / len(wins) if wins else 0
    avg_l = sum(t[1] * troy_oz for t in losses) / len(losses) if losses else 0
    total_pnl = sum(t[1] * troy_oz for t in trades)
    
    verdict = "PROFIT" if total_pnl > 0 else "LOSS"
    emoji = "+" if total_pnl > 0 else ""
    
    print(f"{label:<18} {total:>6} {wr:>6.1f}% ${avg_w:>8.2f} ${avg_l:>9.2f} {emoji}${total_pnl:>9.2f}  {'<<< ' + verdict if total_pnl > 0 else verdict}")

print("=" * 80)
