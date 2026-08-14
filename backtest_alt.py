"""Test different strategies on different assets to find the best combo."""
import yfinance as yf, pandas as pd

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - (100 / (1 + g.ewm(com=p-1,min_periods=p).mean() / l.ewm(com=p-1,min_periods=p).mean()))
def atr(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, min_periods=p).mean()

assets = [
    ("CL=F", "Oil (WTI)", 10),
    ("SI=F", "Silver", 10),
    ("NG=F", "Nat Gas", 10),
    ("EURUSD=X", "EUR/USD", 30),
    ("GBPUSD=X", "GBP/USD", 30),
    ("^GSPC", "S&P 500", 20),
]

def run_strategy(df, strategy, leverage):
    exposure = 400 * leverage
    avg_price = float(df["Close"].mean())
    units = exposure / avg_price
    
    df["RSI"] = rsi(df["Close"]); df["ATR"] = atr(df["High"], df["Low"], df["Close"])
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["Support"] = df["Low"].rolling(20).min()
    df["Resistance"] = df["High"].rolling(20).max()
    sma20 = df["Close"].rolling(20).mean()
    std20 = df["Close"].rolling(20).std()
    df["BB_Upper"] = sma20 + (std20 * 2)
    df["BB_Lower"] = sma20 - (std20 * 2)
    df["BB_Mid"] = sma20
    
    trades = []; in_t = False; ep = sl = tp = 0; direction = None
    
    for i in range(200, len(df)):
        row = df.iloc[i]
        p = float(row["Close"]); h = float(row["High"]); lo = float(row["Low"])
        r = float(row["RSI"]); a = float(row["ATR"])
        ema20 = float(row["EMA20"]); ema50 = float(row["EMA50"])
        sup = float(row["Support"]); res = float(row["Resistance"])
        bb_u = float(row["BB_Upper"]); bb_l = float(row["BB_Lower"]); bb_m = float(row["BB_Mid"])
        if pd.isna(r) or pd.isna(a) or pd.isna(ema50) or pd.isna(bb_u): continue
        
        if in_t:
            if direction == "long":
                if h >= tp: trades.append(("W", tp-ep)); in_t = False
                elif lo <= sl: trades.append(("L", sl-ep)); in_t = False
            else:
                if lo <= tp: trades.append(("W", ep-tp)); in_t = False
                elif h >= sl: trades.append(("L", ep-sl)); in_t = False
        else:
            if strategy == "breakout":
                # Breakout: buy above resistance, sell below support
                if p > res and r > 50 and p > ema50:
                    ep=p; sl=p-(a*1.0); tp=p+(a*2.0); direction="long"; in_t=True
                elif p < sup and r < 50 and p < ema50:
                    ep=p; sl=p+(a*1.0); tp=p-(a*2.0); direction="short"; in_t=True
                    
            elif strategy == "ema_cross":
                # EMA crossover: buy when 20 crosses above 50, sell when below
                prev_ema20 = float(df["EMA20"].iloc[i-1])
                prev_ema50 = float(df["EMA50"].iloc[i-1])
                if prev_ema20 <= prev_ema50 and ema20 > ema50:  # bullish cross
                    ep=p; sl=p-(a*1.0); tp=p+(a*2.0); direction="long"; in_t=True
                elif prev_ema20 >= prev_ema50 and ema20 < ema50:  # bearish cross
                    ep=p; sl=p+(a*1.0); tp=p-(a*2.0); direction="short"; in_t=True
                    
            elif strategy == "momentum":
                # Momentum: strong RSI + trend alignment
                if r > 60 and r < 80 and p > ema50 and p > ema20:
                    ep=p; sl=p-(a*0.75); tp=p+(a*2.0); direction="long"; in_t=True
                elif r < 40 and r > 20 and p < ema50 and p < ema20:
                    ep=p; sl=p+(a*0.75); tp=p-(a*2.0); direction="short"; in_t=True
                    
            elif strategy == "mean_revert":
                # Bollinger bounce
                if p < bb_l and r < 25:
                    ep=p; sl=bb_l-(a*0.5); tp=bb_m; direction="long"; in_t=True
                elif p > bb_u and r > 75:
                    ep=p; sl=bb_u+(a*0.5); tp=bb_m; direction="short"; in_t=True

    w = [t for t in trades if t[0]=="W"]; tot = len(trades)
    if tot == 0: return None
    wr = len(w)/tot*100
    pnl = sum(t[1] * units for t in trades)
    return {"trades": tot, "wr": wr, "pnl": pnl, "per_day": tot/60}

strategies = ["breakout", "ema_cross", "momentum", "mean_revert"]

print(f"{'Asset':<10} {'Strategy':<13} {'Lev':>4} {'Trades':>7} {'WR':>6} {'P&L':>10} {'$/day':>7}")
print("=" * 65)

results = []
for ticker, name, leverage in assets:
    try:
        df = yf.download(ticker, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        if df.empty or len(df) < 250: continue
        
        for strat in strategies:
            r = run_strategy(df.copy(), strat, leverage)
            if r and r["trades"] >= 5:
                results.append((name, strat, leverage, r))
                marker = " <<<" if r["pnl"] > 200 else ""
                print(f"{name:<10} {strat:<13} x{leverage:<3} {r['trades']:>7} {r['wr']:>5.1f}% ${r['pnl']:>9.2f} ${r['pnl']/60:>6.2f}{marker}")
    except Exception as e:
        pass

print("\n" + "=" * 65)
print("TOP 5 MOST PROFITABLE:")
results.sort(key=lambda x: x[3]["pnl"], reverse=True)
for name, strat, lev, r in results[:5]:
    print(f"  {name} + {strat} (x{lev}): ${r['pnl']:.2f} | {r['trades']} trades | {r['wr']:.0f}% WR")
