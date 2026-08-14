"""Backtest Strategy 1 on 6 months of data (includes uptrend AND downtrend)."""
import yfinance as yf, pandas as pd

df = yf.download("GC=F", period="6mo", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
print(f"Data: {len(df)} candles | {df.index[0].date()} to {df.index[-1].date()}")

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1,min_periods=p).mean() / l.ewm(com=p-1,min_periods=p).mean()))
def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

df["RSI"] = rsi(df["Close"]); df["ATR"] = atr(df["High"], df["Low"], df["Close"])
df["Support"] = df["Low"].rolling(20).min()
df["Resistance"] = df["High"].rolling(20).max()
df["EMA50"] = df["Close"].ewm(span=50).mean()

SESSIONS = {"London": (7,16), "US": (13,22)}
exposure = 8000  # $400 x 20

trades = []; in_t = False; ep = sl = tp = 0; direction = None
equity = 0; peak = 0; max_dd = 0
monthly_pnl = {}

for i in range(200, len(df)):
    row = df.iloc[i]
    p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
    r = float(row["RSI"]); a = float(row["ATR"])
    sup = float(row["Support"]); res = float(row["Resistance"])
    ema = float(row["EMA50"])
    if pd.isna(r) or pd.isna(a) or pd.isna(ema): continue
    hr = row.name.hour
    ins = any(s <= hr < e for s, e in SESSIONS.values())

    if in_t:
        if direction == "long":
            if h >= tp: 
                pnl = (tp-ep) * (exposure/ep)
                trades.append(("WIN", pnl, row.name))
                equity += pnl; in_t = False
            elif lo <= sl: 
                pnl = (sl-ep) * (exposure/ep)
                trades.append(("LOSS", pnl, row.name))
                equity += pnl; in_t = False
        else:
            if lo <= tp: 
                pnl = (ep-tp) * (exposure/ep)
                trades.append(("WIN", pnl, row.name))
                equity += pnl; in_t = False
            elif h >= sl: 
                pnl = (ep-sl) * (exposure/ep)
                trades.append(("LOSS", pnl, row.name))
                equity += pnl; in_t = False
        if equity > peak: peak = equity
        if peak - equity > max_dd: max_dd = peak - equity
    else:
        if not ins or hr >= 21: continue
        # BUY: above EMA50 + near support + RSI < 65
        if p > ema and p <= sup+(a*8) and r < 65:
            ep=p; sl=p-(a*0.75); tp=p+(a*1.5); direction="long"; in_t=True
        # SELL: below EMA50 + near resistance + RSI > 35
        elif p < ema and p >= res-(a*8) and r > 35:
            ep=p; sl=p+(a*0.75); tp=p-(a*1.5); direction="short"; in_t=True

# Monthly breakdown
for outcome, pnl, dt in trades:
    month = dt.strftime("%Y-%m")
    monthly_pnl[month] = monthly_pnl.get(month, 0) + pnl

wins = [t for t in trades if t[0]=="WIN"]
losses = [t for t in trades if t[0]=="LOSS"]
tot = len(trades)
wr = len(wins)/tot*100 if tot > 0 else 0
total_pnl = equity
avg_w = sum(t[1] for t in wins)/len(wins) if wins else 0
avg_l = sum(t[1] for t in losses)/len(losses) if losses else 0

print(f"\n{'='*60}")
print(f"STRATEGY 1 — 6 MONTHS BACKTEST")
print(f"{'='*60}")
print(f"Total trades  : {tot}")
print(f"Wins          : {len(wins)}")
print(f"Losses        : {len(losses)}")
print(f"Win rate      : {wr:.1f}%")
print(f"Avg win       : ${avg_w:.2f}")
print(f"Avg loss      : ${avg_l:.2f}")
print(f"Total P&L     : ${total_pnl:.2f}")
print(f"Per month     : ${total_pnl/6:.2f}")
print(f"Peak equity   : ${peak:.2f}")
print(f"Max drawdown  : ${max_dd:.2f}")
print(f"Trades/day    : {tot/180:.1f}")

print(f"\n--- Monthly Breakdown ---")
for month in sorted(monthly_pnl.keys()):
    pnl = monthly_pnl[month]
    bar = "+" * int(pnl/50) if pnl > 0 else "-" * int(abs(pnl)/50)
    print(f"  {month}: ${pnl:>8.2f}  {bar}")

# Direction breakdown
longs = [t for t in trades if t not in losses]  # rough
long_trades = [(o, p, d) for o, p, d in trades if d.month <= 3]  # Q1 = mostly uptrend
short_trades = [(o, p, d) for o, p, d in trades if d.month >= 5]  # Q2+ = downtrend

print(f"\n--- Period Breakdown ---")
q1 = [t for t in trades if t[2].month <= 3]
q2 = [t for t in trades if t[2].month >= 4]
if q1:
    q1_pnl = sum(t[1] for t in q1)
    q1_wr = len([t for t in q1 if t[0]=="WIN"])/len(q1)*100
    print(f"  Jan-Mar (uptrend) : {len(q1)} trades | WR: {q1_wr:.0f}% | P&L: ${q1_pnl:.2f}")
if q2:
    q2_pnl = sum(t[1] for t in q2)
    q2_wr = len([t for t in q2 if t[0]=="WIN"])/len(q2)*100
    print(f"  Apr-Jul (downtrend): {len(q2)} trades | WR: {q2_wr:.0f}% | P&L: ${q2_pnl:.2f}")

print(f"\n{'='*60}")
