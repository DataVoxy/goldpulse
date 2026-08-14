"""
Generate silver_dashboard_data.json for the SilverPulse dashboard.
Run after each silver strategy cycle or on a schedule.
"""
import json
import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).parent / "silver_dashboard_data.json"


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


def get_gold_silver_ratio():
    """Calculate current and average gold/silver ratio."""
    try:
        gold = yf.download("GC=F", period="10d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(gold.columns, pd.MultiIndex):
            gold.columns = gold.columns.get_level_values(0)

        silver = yf.download("SI=F", period="10d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(silver.columns, pd.MultiIndex):
            silver.columns = silver.columns.get_level_values(0)

        if gold.empty or silver.empty:
            return None, None

        ratio_now = float(gold["Close"].iloc[-1]) / float(silver["Close"].iloc[-1])

        # 5-day average ratio
        if len(gold) >= 40 and len(silver) >= 40:
            gold_avg = float(gold["Close"].iloc[-40:].mean())
            silver_avg = float(silver["Close"].iloc[-40:].mean())
            ratio_avg = gold_avg / silver_avg
        else:
            ratio_avg = ratio_now

        return round(ratio_now, 1), round(ratio_avg, 1)
    except Exception:
        return None, None


def main():
    # Silver data
    df = yf.download("SI=F", period="30d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty or len(df) < 50:
        print("Not enough silver data")
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

    # Trend
    ema20 = float(close.ewm(span=20).mean().iloc[-1])
    trend_up = price > ema50

    # Gold/Silver ratio
    gs_ratio, gs_ratio_avg = get_gold_silver_ratio()

    # DXY
    try:
        dxy_df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(dxy_df.columns, pd.MultiIndex):
            dxy_df.columns = dxy_df.columns.get_level_values(0)
        dxy = round(float(dxy_df["Close"].iloc[-1]), 2)
    except Exception:
        dxy = None

    data = {
        "price": round(price, 2),
        "change_pct": round(change_pct, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 3),
        "macd_hist": round(macd_hist, 4),
        "ema50": round(ema50, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "trend_up": trend_up,
        "gold_silver_ratio": gs_ratio,
        "gold_silver_ratio_avg": gs_ratio_avg,
        "session": get_session(),
        "dxy": dxy,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    OUTPUT.write_text(json.dumps(data, indent=2))
    print(f"SilverPulse dashboard: ${price:.2f} | G/S ratio: {gs_ratio} | RSI {rsi:.1f}")

    # Auto-upload to R2 if configured
    try:
        upload_script = Path(__file__).parent.parent / "deploy" / "upload_to_r2.py"
        if upload_script.exists() and os.environ.get("CF_ACCOUNT_ID"):
            import subprocess
            result = subprocess.run(
                ["py", str(upload_script), "--silver"],
                capture_output=True, timeout=15, text=True
            )
            if result.returncode == 0:
                print("R2 upload OK")
    except Exception as e:
        print(f"R2 upload error: {e}")


if __name__ == "__main__":
    main()
