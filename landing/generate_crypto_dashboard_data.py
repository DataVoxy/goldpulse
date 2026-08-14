"""
Generate crypto_dashboard_data.json for the CryptoPulse dashboard.
"""
import json
import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).parent / "crypto_dashboard_data.json"


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


def get_fear_greed():
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = resp.json()
        return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
    except Exception:
        return None, None


def get_crypto_stats(ticker):
    """Get indicators for one crypto asset."""
    df = yf.download(ticker, period="30d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty or len(df) < 50:
        return None

    close = df["Close"]
    price = float(close.iloc[-1])

    # 24h change
    change = ((price - float(close.iloc[-24])) / float(close.iloc[-24])) * 100 if len(close) >= 24 else 0

    # Indicators
    rsi = float(compute_rsi(close).iloc[-1])
    atr = float(compute_atr(df["High"], df["Low"], close).iloc[-1])
    ema50 = float(close.ewm(span=50).mean().iloc[-1])
    ema200 = float(close.ewm(span=200).mean().iloc[-1]) if len(close) >= 200 else ema50

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_hist = float((ema12 - ema26).iloc[-1] - (ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])

    # Levels
    support = float(df["Low"].iloc[-24:].min())
    resistance = float(df["High"].iloc[-24:].max())

    return {
        "price": round(price, 2),
        "change": round(change, 1),
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "macd": round(macd_hist, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "golden_cross": ema50 > ema200,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "trend_up": price > ema50,
    }


def main():
    btc = get_crypto_stats("BTC-USD")
    eth = get_crypto_stats("ETH-USD")

    if not btc or not eth:
        print("Failed to get crypto data")
        return

    fg_value, fg_label = get_fear_greed()

    # BTC dominance estimate
    btc_dom = None
    try:
        btc_mcap = btc["price"] * 19_500_000
        eth_mcap = eth["price"] * 120_000_000
        btc_dom = round(btc_mcap / (btc_mcap + eth_mcap) * 100, 1)
    except Exception:
        pass

    data = {
        "btc_price": btc["price"],
        "btc_change": btc["change"],
        "btc_rsi": btc["rsi"],
        "btc_atr": btc["atr"],
        "btc_macd": btc["macd"],
        "btc_ema50": btc["ema50"],
        "btc_ema200": btc["ema200"],
        "btc_golden_cross": btc["golden_cross"],
        "btc_support": btc["support"],
        "btc_resistance": btc["resistance"],
        "btc_trend_up": btc["trend_up"],
        "eth_price": eth["price"],
        "eth_change": eth["change"],
        "eth_rsi": eth["rsi"],
        "eth_atr": eth["atr"],
        "eth_macd": eth["macd"],
        "eth_ema50": eth["ema50"],
        "eth_golden_cross": eth["golden_cross"],
        "fear_greed": fg_value,
        "fear_greed_label": fg_label,
        "btc_dominance": btc_dom,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    OUTPUT.write_text(json.dumps(data, indent=2))
    print(f"CryptoPulse dashboard: BTC ${btc['price']:,.0f} | ETH ${eth['price']:,.0f} | F&G {fg_value}")

    # Auto-upload to R2
    try:
        upload_script = Path(__file__).parent.parent / "deploy" / "upload_to_r2.py"
        if upload_script.exists() and os.environ.get("CF_ACCOUNT_ID"):
            import subprocess
            subprocess.run(["py", str(upload_script), "--crypto"], capture_output=True, timeout=15)
    except Exception:
        pass


if __name__ == "__main__":
    main()
