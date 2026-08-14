"""Backtest: ADX filter comparison with 4h force-close."""
import yfinance as yf, pandas as pd

df = yf.download("GC=F", period="6mo", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
print(f"Data: {len(df)} candles | {df.index[0].date()} to {df.index[-1].date()}")

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100/(1 + g.ewm(com=p-1,min_periods=p).mean()/l.ewm(com=p-1,min_periods=p).mean()))

def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

def adx(h, l, c, p=14):
    plus_dm = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.ewm(com=p-1, min_periods=p).mean()
    plus_di = 100 * plus_dm.ewm(com=p-1, min_periods=p).mean() / atr_val
    minus_di = 100 * minus_dm.ewm(com=p-1, min_periods=p).mean() / atr_val
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"])
df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["ADX"] = adx(df["High"], df["Low"], df["Close"])

SESSIONS = {"London": (7,16), "US": (13,22)}
EXPOSURE = 8000
MAX_DUR = 4

def run(use_adx=False, adx_min=20):
    trades = []; in_t = False; ep = sl = tp = 0; d = None; eb = 0
    for i in range(200, len(df)):
        row = df.iloc[i]
        p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
        r = float(row["RSI"]); a = float(row["ATR"]); sup = float(row["Support"])
        res = float(row["Resistance"]); ema = float(row["EMA50"]); adx_val = float(row["ADX"])
        if pd.isna(r) or pd.isna(a) or pd.isna(ema) or pd.isna(adx_val): continue
        hr = row.name.hour
        ins = any(s <= hr < e for s, e in SESSIONS.values())
        if in_t:
            bars = i - eb
            if bars >= MAX_DUR:
                pnl = ((p - ep) if d == "long" else (ep - p)) * (EXPOSURE / ep)
                trades.append(("WIN" if pnl > 0 else "LOSS", pnl))
                in_t = False; continue
            if d == "long":
                if h >= tp: trades.append(("WIN", (tp-ep)*(EXPOSURE/ep))); in_t = False
                elif lo <= sl: trades.append(("LOSS", (sl-ep)*(EXPOSURE/ep))); in_t = False
            else:
                if lo <= tp: trades.append(("WIN", (ep-tp)*(EXPOSURE/ep))); in_t = False
                elif h >= sl: trades.append(("LOSS", (ep-sl)*(EXPOSURE/ep))); in_t = False
        else:
            if not ins or hr >= 21: continue
            if use_adx and adx_val < adx_min: continue
            if p > ema and p <= sup + (a*8) and r < 65:
                ep = p; sl = p-(a*1.2); tp = p+(a*2.0); d = "long"; in_t = True; eb = i
            elif p < ema and p >= res - (a*8) and r > 35:
                ep = p; sl = p+(a*1.2); tp = p-(a*2.0); d = "short"; in_t = True; eb = i
    return trades

t1 = run(False)
t2 = run(True, 20)
t3 = run(True, 25)

def stats(name, t):
    w = [x for x in t if x[0] == "WIN"]
    pnl = sum(x[1] for x in t)
    wr = len(w)/len(t)*100 if t else 0
    eq = 0; peak = 0; dd = 0
    for x in t:
        eq += x[1]
        if eq > peak: peak = eq
        if peak - eq > dd: dd = peak - eq
    top3 = sorted(t, key=lambda x: x[1], reverse=True)
    pnl_no3 = sum(x[1] for x in top3[3:])
    robust = "ROBUST" if pnl_no3 > 0 else "NOT ROBUST"
    print(f"  {name}")
    print(f"    Trades: {len(t)} | Wins: {len(w)} | Losses: {len(t)-len(w)} | WR: {wr:.1f}%")
    print(f"    PnL: ${pnl:.2f} | Max DD: ${dd:.2f} | Without top 3: ${pnl_no3:.2f} ({robust})")
    print()

print("\n6-MONTH BACKTEST — 4h force-close — ADX FILTER COMPARISON")
print("=" * 60)
stats("Zonder ADX filter (huidige strategie)", t1)
stats("Met ADX > 20 filter", t2)
stats("Met ADX > 25 filter", t3)
