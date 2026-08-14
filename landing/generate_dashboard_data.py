"""
Generate dashboard_data.json for the public gold market dashboard.
Run after each strategy cycle or on a schedule.
No signals, no trades — just market data and indicators.
"""
import json
import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).parent / "dashboard_data.json"


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(high, low, close, period=14):
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def get_session():
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 13:
        return "London"
    elif 13 <= hour < 16:
        return "London + US"
    elif 16 <= hour < 22:
        return "US"
    else:
        return "Closed"


def main():
    # Gold data
    df = yf.download("GC=F", period="30d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty or len(df) < 50:
        print("Not enough data")
        return

    close = df["Close"]
    price = float(close.iloc[-1])

    # Daily change
    today = df.tail(24)
    open_price = float(today["Open"].iloc[0])
    change_pct = ((price - open_price) / open_price) * 100

    # Indicators
    rsi = float(compute_rsi(close).iloc[-1])
    atr = float(compute_atr(df["High"], df["Low"], close).iloc[-1])
    ema50 = float(close.ewm(span=50).mean().iloc[-1])

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_hist = float((ema12 - ema26).iloc[-1] - (ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])

    # Levels
    support = float(df["Low"].iloc[-20:].min())
    resistance = float(df["High"].iloc[-20:].max())
    day_range = float(today["High"].max()) - float(today["Low"].min())

    # Trend
    ema20 = float(close.ewm(span=20).mean().iloc[-1])
    if change_pct > 0.5 and ema20 > ema50:
        trend, strength = "Bullish", 4
    elif change_pct > 0.1:
        trend, strength = "Slightly Bullish", 3
    elif change_pct < -0.5 and ema20 < ema50:
        trend, strength = "Bearish", 4
    elif change_pct < -0.1:
        trend, strength = "Slightly Bearish", 3
    else:
        trend, strength = "Ranging", 2

    # DXY
    try:
        dxy_df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(dxy_df.columns, pd.MultiIndex):
            dxy_df.columns = dxy_df.columns.get_level_values(0)
        dxy = round(float(dxy_df["Close"].iloc[-1]), 2)
        dxy_avg = round(float(dxy_df["Close"].rolling(20).mean().iloc[-1]), 2) if len(dxy_df) >= 20 else dxy
        dxy_weak = dxy < dxy_avg
    except Exception:
        dxy, dxy_weak = None, False

    # US 10Y
    try:
        tnx_df = yf.download("^TNX", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(tnx_df.columns, pd.MultiIndex):
            tnx_df.columns = tnx_df.columns.get_level_values(0)
        us10y = round(float(tnx_df["Close"].iloc[-1]), 2)
        us10y_avg = round(float(tnx_df["Close"].rolling(20).mean().iloc[-1]), 2) if len(tnx_df) >= 20 else us10y
        yields_falling = us10y < us10y_avg
    except Exception:
        us10y, yields_falling = None, False

    data = {
        "price": round(price, 2),
        "change_pct": round(change_pct, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "macd_hist": round(macd_hist, 4),
        "ema50": round(ema50, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "range": round(day_range, 2),
        "trend": trend,
        "trend_strength": strength,
        "session": get_session(),
        "dxy": dxy,
        "dxy_weak": dxy_weak,
        "us10y": us10y,
        "yields_falling": yields_falling,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    OUTPUT.write_text(json.dumps(data, indent=2))
    print(f"Dashboard data written: ${price:.2f} | {trend} | RSI {rsi:.1f}")

    # Auto-upload to R2 if credentials are configured
    try:
        upload_script = Path(__file__).parent.parent / "deploy" / "upload_to_r2.py"
        if upload_script.exists() and os.environ.get("CF_ACCOUNT_ID"):
            import subprocess
            result = subprocess.run(
                ["py", str(upload_script)],
                capture_output=True, timeout=15, text=True
            )
            if result.returncode == 0:
                print("R2 upload OK")
            else:
                print(f"R2 upload failed: {result.stderr[:100]}")
    except Exception as e:
        print(f"R2 upload error: {e}")


if __name__ == "__main__":
    main()
