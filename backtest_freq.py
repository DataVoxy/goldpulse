"""Test frequency vs profitability."""
import yfinance as yf, pandas as pd

df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1,min_periods=p).mean() / l.ewm(com=p-1,min_periods=p).mean()))
def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"]); df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min(); df["Resistance"] = df["High"].rolling(20).max()
df["EMA50"] = df["Close"].ewm(span=50).mean()

SESSIONS = {"London": (7,16), "US": (13,22)}

print(f"{'Config':<35} {'Trades':>6} {'Per Day':>7} {'WR':>6} {'P&L':>10} {'MaxDD':>8}")
print("-" * 80)

# Test different RSI thresholds and support distances
for rsi_thresh, sup_mult, label in [
    (65, 5, "RSI<65 sup*5 (current)"),
    (70, 5, "RSI<70 sup*5"),
    (75, 5, "RSI<75 sup*5"),
    (65, 8, "RSI<65 sup*8 (wider)"),
    (70, 8, "RSI<70 sup*8"),
    (75, 10, "RSI<75 sup*10 (very loose)"),
    (80, 10, "RSI<80 sup*10 (max trades)"),
]:
    trades = []; in_t = False; ep = sl = tp = 0; direction = None
    equity = 0; peak = 0; max_dd = 0
    sl_mult = 0.75; tp_mult = 1.5

    for i in range(200, len(df)):
        row = df.iloc[i]
        p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
        r = float(row["RSI"]); a = float(row["ATR"])
        sup = float(row["Support"]); res = float(row["Resistance"])
        ema50 = float(row["EMA50"]); hr = row.name.hour
        if pd.isna(r) or pd.isna(a) or pd.isna(sup) or pd.isna(ema50): continue
        ins = any(s <= hr < e for s, e in SESSIONS.values())

        if in_t:
            if direction == "long":
                if h >= tp: trades.append(("W", tp-ep)); equity += (tp-ep)*1.95; in_t = False
                elif lo <= sl: trades.append(("L", sl-ep)); equity += (sl-ep)*1.95; in_t = False
            else:
                if lo <= tp: trades.append(("W", ep-tp)); equity += (ep-tp)*1.95; in_t = False
                elif h >= sl: trades.append(("L", ep-sl)); equity += (ep-sl)*1.95; in_t = False
            if equity > peak: peak = equity
            if peak - equity > max_dd: max_dd = peak - equity
        else:
            if not ins or hr >= 21: continue
            # BUY: price above EMA50 + near support + RSI low
            if p > ema50 and p <= sup + (a * sup_mult) and r < rsi_thresh:
                ep = p; sl = p - (a*sl_mult); tp = p + (a*tp_mult); direction = "long"; in_t = True
            # SELL: price below EMA50 + near resistance + RSI high
            elif p < ema50 and p >= res - (a * sup_mult) and r > (100 - rsi_thresh):
                ep = p; sl = p + (a*sl_mult); tp = p - (a*tp_mult); direction = "short"; in_t = True

    w = [t for t in trades if t[0]=="W"]; tot = len(trades)
    if tot == 0: print(f"{label:<35} {'0':>6}"); continue
    wr = len(w)/tot*100; pnl = equity; per_day = tot / 60
    marker = " <<<" if pnl > 0 else ""
    print(f"{label:<35} {tot:>6} {per_day:>6.1f}/d {wr:>5.1f}% ${pnl:>9.2f} ${max_dd:>7.2f}{marker}")
