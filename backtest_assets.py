"""Compare Strategy 1 across different assets."""
import yfinance as yf, pandas as pd

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1,min_periods=p).mean() / l.ewm(com=p-1,min_periods=p).mean()))
def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

# Assets with eToro leverage
assets = [
    ("GC=F", "Gold", 20),
    ("SI=F", "Silver", 10),
    ("CL=F", "Oil (WTI)", 10),
    ("^GSPC", "S&P 500", 20),
    ("EURUSD=X", "EUR/USD", 30),
    ("GBPUSD=X", "GBP/USD", 30),
    ("^GDAXI", "DAX", 20),
]

print(f"{'Asset':<12} {'Leverage':>8} {'Trades':>7} {'WR':>6} {'P&L ($400)':>11} {'Per Day':>8}")
print("-" * 60)

for ticker, name, leverage in assets:
    try:
        df = yf.download(ticker, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        if df.empty or len(df) < 250: 
            print(f"{name:<12} {'x'+str(leverage):>8} {'NO DATA':>7}")
            continue

        df["RSI"] = rsi(df["Close"]); df["ATR"] = atr(df["High"], df["Low"], df["Close"])
        df["Support"] = df["Low"].rolling(20).min()
        df["Resistance"] = df["High"].rolling(20).max()
        df["EMA50"] = df["Close"].ewm(span=50).mean()

        exposure = 400 * leverage
        trades = []; in_t = False; ep = sl = tp = 0; direction = None

        for i in range(200, len(df)):
            row = df.iloc[i]
            p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
            r = float(row["RSI"]); a = float(row["ATR"])
            sup = float(row["Support"]); res = float(row["Resistance"])
            ema = float(row["EMA50"])
            if pd.isna(r) or pd.isna(a) or pd.isna(ema): continue

            if in_t:
                if direction == "long":
                    if h >= tp: trades.append(("W", tp-ep)); in_t = False
                    elif lo <= sl: trades.append(("L", sl-ep)); in_t = False
                else:
                    if lo <= tp: trades.append(("W", ep-tp)); in_t = False
                    elif h >= sl: trades.append(("L", ep-sl)); in_t = False
            else:
                hr = row.name.hour if hasattr(row.name, 'hour') else 12
                if hr >= 21 or hr < 7: continue
                if p > ema and p <= sup+(a*8) and r < 65:
                    ep=p; sl=p-(a*0.75); tp=p+(a*1.5); direction="long"; in_t=True
                elif p < ema and p >= res-(a*8) and r > 35:
                    ep=p; sl=p+(a*0.75); tp=p-(a*1.5); direction="short"; in_t=True

        w = [t for t in trades if t[0]=="W"]; tot = len(trades)
        if tot == 0:
            print(f"{name:<12} {'x'+str(leverage):>8} {'0':>7}")
            continue
        wr = len(w)/tot*100
        avg_price = float(df["Close"].mean())
        units = exposure / avg_price
        pnl = sum(t[1] * units for t in trades)
        per_day = tot / 60
        print(f"{name:<12} {'x'+str(leverage):>8} {tot:>7} {wr:>5.1f}% ${pnl:>10.2f} {per_day:>7.1f}/d")
    except Exception as e:
        print(f"{name:<12} ERROR: {e}")
