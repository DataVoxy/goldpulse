"""Backtest Strategy 2: SnapBack (Bollinger Band mean reversion)."""
import yfinance as yf, pandas as pd

df = yf.download("GC=F", period="60d", interval="1h", progress=False, auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

close = df["Close"]; high = df["High"]; low = df["Low"]
sma = close.rolling(20).mean()
std = close.rolling(20).std()
upper = sma + (std * 2); lower = sma - (std * 2)
tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
atr = tr.ewm(com=13, min_periods=14).mean()
delta = close.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
rsi = 100 - (100 / (1 + gain.ewm(com=13,min_periods=14).mean() / loss.ewm(com=13,min_periods=14).mean()))

SESSIONS = {"London": (7,16), "US": (13,22)}
trades = []; in_t = False; ep = sl = tp = 0; direction = None

for i in range(30, len(df)):
    row = df.iloc[i]
    p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
    u = float(upper.iloc[i]); m = float(sma.iloc[i]); l = float(lower.iloc[i])
    a = float(atr.iloc[i]); r = float(rsi.iloc[i])
    hr = row.name.hour
    ins = any(s <= hr < e for s, e in SESSIONS.values())
    if pd.isna(u) or pd.isna(a) or pd.isna(r): continue

    if in_t:
        if direction == "long":
            if h >= tp: trades.append(("WIN", tp-ep)); in_t = False
            elif lo <= sl: trades.append(("LOSS", sl-ep)); in_t = False
        else:
            if lo <= tp: trades.append(("WIN", ep-tp)); in_t = False
            elif h >= sl: trades.append(("LOSS", ep-sl)); in_t = False
    else:
        if not ins or hr >= 21: continue
        # BUY: below lower band + RSI < 30
        if p < l and r < 30:
            ep = p; sl = l - a; tp = m; direction = "long"; in_t = True
        # SELL: above upper band + RSI > 70
        elif p > u and r > 70:
            ep = p; sl = u + a; tp = m; direction = "short"; in_t = True

wins = [t for t in trades if t[0]=="WIN"]
losses = [t for t in trades if t[0]=="LOSS"]
tot = len(trades)
if tot == 0:
    print("No trades generated")
else:
    wr = len(wins)/tot*100; toz = 8000/4100
    pnl = sum(t[1]*toz for t in trades)
    aw = sum(t[1]*toz for t in wins)/len(wins) if wins else 0
    al = sum(t[1]*toz for t in losses)/len(losses) if losses else 0
    print(f"SnapBack Strategy — 60 days")
    print(f"Trades: {tot} | WR: {wr:.1f}% | Avg Win: +${aw:.2f} | Avg Loss: -${abs(al):.2f}")
    print(f"Total P&L: ${pnl:.2f} | Per day: ${pnl/60:.2f}")
    print(f"Wins: {len(wins)} | Losses: {len(losses)}")
