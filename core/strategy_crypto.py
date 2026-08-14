"""
CryptoPulse Strategy
--------------------
Algorithmic BTC/ETH trading signals via Telegram.
Same approach as GoldPulse but tuned for crypto volatility and 24/7 market.

Tickers: BTC-USD, ETH-USD (Yahoo Finance)
"""

import yfinance as yf
import pandas as pd
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ==========================
# CONFIG
# ==========================
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SYMBOLS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}
PRODUCT_NAME = "CryptoPulse"
ATR_PERIOD = 14
RSI_PERIOD = 14
RSI_OVERSOLD = 35           # Crypto is more volatile — classic levels work
RSI_OVERBOUGHT = 65
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Position settings (generic — user adjusts to their platform)
MARGIN_USD = 500
LEVERAGE = 10
EXPOSURE_USD = MARGIN_USD * LEVERAGE

# Risk
SL_ATR_MULT = 1.5           # Wider for crypto volatility
TP_ATR_MULT = 3.0           # 1:2 risk/reward
CHECK_INTERVAL = 900        # 15 min (crypto is 24/7, faster checks make sense)

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_ID = os.environ.get("CRYPTOPULSE_CHANNEL_ID", "")

# Crypto trades 24/7 — no session filter needed
# But we can track market sentiment periods
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# ==========================
# LOGGING
# ==========================
_log_path = str(Path(__file__).parent.parent / "logs" / "crypto_strategy.log")
os.makedirs(os.path.dirname(_log_path), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_path),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==========================
# DATA
# ==========================
def get_crypto_data(symbol="BTC-USD"):
    """Fetch 1H candles for crypto."""
    for attempt in range(3):
        df = yf.download(symbol, period="30d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty and len(df) >= MACD_SLOW + MACD_SIGNAL + 5:
            return df
        time.sleep(3)
    raise ValueError(f"Not enough data for {symbol}")

# ==========================
# INDICATORS
# ==========================
def compute_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_macd(series):
    ema_fast = series.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = series.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram = macd_line - signal
    return macd_line, signal, histogram

def compute_atr(high, low, close, period=ATR_PERIOD):
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

def compute_support(df, lookback=24):
    return float(df["Low"].iloc[-lookback:].min())

def compute_resistance(df, lookback=24):
    return float(df["High"].iloc[-lookback:].max())

# ==========================
# FEAR & GREED INDEX
# ==========================
def get_fear_greed():
    """Get crypto Fear & Greed index (0-100)."""
    try:
        import requests
        resp = requests.get(FEAR_GREED_URL, timeout=5)
        data = resp.json()
        value = int(data["data"][0]["value"])
        label = data["data"][0]["value_classification"]
        return value, label
    except Exception:
        return None, None

# ==========================
# BTC DOMINANCE
# ==========================
def get_btc_dominance():
    """Estimate BTC dominance from BTC vs total market (simplified)."""
    try:
        btc = yf.download("BTC-USD", period="2d", interval="1h", progress=False, auto_adjust=True)
        eth = yf.download("ETH-USD", period="2d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(btc.columns, pd.MultiIndex):
            btc.columns = btc.columns.get_level_values(0)
        if isinstance(eth.columns, pd.MultiIndex):
            eth.columns = eth.columns.get_level_values(0)
        btc_price = float(btc["Close"].iloc[-1])
        eth_price = float(eth["Close"].iloc[-1])
        # Rough dominance estimate
        btc_mcap = btc_price * 19_500_000  # ~19.5M BTC supply
        eth_mcap = eth_price * 120_000_000  # ~120M ETH supply
        dominance = btc_mcap / (btc_mcap + eth_mcap) * 100
        return round(dominance, 1)
    except Exception:
        return None

# ==========================
# ENTRY LOGIC
# ==========================
def evaluate_entry(df, symbol_name="BTC"):
    """Evaluate crypto entry signal."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    rsi = compute_rsi(close)
    macd_line, sig, hist = compute_macd(close)
    atr = compute_atr(high, low, close)

    last_close = float(close.iloc[-1])
    last_rsi = float(rsi.iloc[-1])
    last_hist = float(hist.iloc[-1])
    last_atr = float(atr.iloc[-1])

    support = compute_support(df)
    resistance = compute_resistance(df)

    # Position sizing
    units = EXPOSURE_USD / last_close
    pip_value = units

    # SL/TP
    sl_pts = last_atr * SL_ATR_MULT
    tp_pts = last_atr * TP_ATR_MULT
    entry_price = last_close

    # Trend
    ema50 = float(close.ewm(span=50).mean().iloc[-1])
    ema200 = float(close.ewm(span=200).mean().iloc[-1]) if len(close) >= 200 else ema50
    trend_up = last_close > ema50
    trend_down = last_close < ema50
    golden_cross = ema50 > ema200

    # Entry conditions
    near_support = last_close <= support + (last_atr * 3)
    near_resistance = last_close >= resistance - (last_atr * 3)
    rsi_oversold = last_rsi < RSI_OVERSOLD
    rsi_overbought = last_rsi > RSI_OVERBOUGHT

    # Default levels
    stop_loss = entry_price - sl_pts
    take_profit = entry_price + tp_pts

    # Signal
    if trend_up and near_support and rsi_oversold:
        signal = "ENTER LONG"
        stop_loss = entry_price - sl_pts
        take_profit = entry_price + tp_pts
    elif trend_down and near_resistance and rsi_overbought:
        signal = "ENTER SHORT"
        stop_loss = entry_price + sl_pts
        take_profit = entry_price - tp_pts
    else:
        signal = "NO TRADE - conditions not met"

    # Dollar P&L
    sl_dollars = sl_pts * pip_value
    tp_dollars = tp_pts * pip_value
    ratio = round(tp_dollars / sl_dollars, 1) if sl_dollars > 0 else 0

    # 24h change
    if len(close) >= 24:
        change_24h = ((last_close - float(close.iloc[-24])) / float(close.iloc[-24])) * 100
    else:
        change_24h = 0

    return {
        "signal": signal,
        "symbol": symbol_name,
        "product": PRODUCT_NAME,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "price": round(entry_price, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "entry": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "sl_dollars": round(sl_dollars, 2),
        "tp_dollars": round(tp_dollars, 2),
        "sl_tp_ratio": f"1:{ratio} (TP:SL)",
        "rsi": round(last_rsi, 1),
        "macd_hist": round(last_hist, 2),
        "atr": round(last_atr, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "golden_cross": golden_cross,
        "near_support": near_support,
        "trend_up": trend_up,
        "change_24h": round(change_24h, 2),
    }

# ==========================
# CHART
# ==========================
_CHART_DIR = Path(__file__).parent.parent / "logs"

def generate_chart(df, symbol="BTC"):
    """Generate crypto price chart."""
    if not HAS_MATPLOTLIB:
        return None
    try:
        os.makedirs(_CHART_DIR, exist_ok=True)
        chart_path = _CHART_DIR / f"crypto_{symbol.lower()}_chart.png"
        recent = df.tail(72).copy()
        if len(recent) < 5:
            return None

        color = "#F7931A" if symbol == "BTC" else "#627EEA"

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(recent.index, recent["Close"], color=color, linewidth=2)
        ax.fill_between(recent.index, recent["Close"], recent["Close"].min() * 0.999, alpha=0.15, color=color)

        support = float(recent["Low"].min())
        resistance = float(recent["High"].max())
        ax.axhline(y=support, color="#ff4444", linestyle="--", alpha=0.7, linewidth=1)
        ax.axhline(y=resistance, color="#44ff44", linestyle="--", alpha=0.7, linewidth=1)

        ax.set_facecolor("#1a1a2e")
        fig.patch.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white", labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("white")
        ax.spines["bottom"].set_color("white")
        ax.set_title(f"{symbol}/USD 1H — ${float(recent['Close'].iloc[-1]):,.0f}", color="white", fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        plt.xticks(rotation=20)
        plt.tight_layout()
        plt.savefig(chart_path, dpi=120, bbox_inches="tight")
        plt.close()
        return str(chart_path)
    except Exception as e:
        log.warning(f"Chart failed: {e}")
        return None

# ==========================
# TELEGRAM
# ==========================
def _post_message(chat_id, msg):
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = req.post(url, data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if resp.status_code == 200:
            log.info(f"Telegram sent to {chat_id}")
        else:
            log.warning(f"Telegram failed: {resp.status_code}")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def _send_photo(chat_id, photo_path):
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as photo:
            req.post(url, data={"chat_id": chat_id}, files={"photo": photo}, timeout=15)
    except Exception:
        pass

def send_telegram(result):
    """Send crypto signal to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    signal = result["signal"]
    symbol = result["symbol"]

    if "ENTER LONG" in signal:
        emoji = "🟢"
    elif "ENTER SHORT" in signal:
        emoji = "🔴"
    else:
        emoji = "⏳"

    # Fear & Greed
    fg_value, fg_label = result.get("fear_greed", (None, None))
    fg_line = f"\n*Fear & Greed:* `{fg_value}` ({fg_label})" if fg_value else ""

    # BTC dominance
    dom = result.get("btc_dominance")
    dom_line = f"\n*BTC Dominance:* `{dom}%`" if dom else ""

    msg = (
        f"🔷 *CryptoPulse — {symbol}*\n"
        f"`{result['timestamp']}`\n\n"
        f"*Signal:* {emoji} {signal}\n"
        f"*24h:* `{result['change_24h']:+.1f}%`{fg_line}{dom_line}\n\n"
        f"*Levels*\n"
        f"Price:       `${result['entry']:,.2f}`\n"
        f"Stop Loss:   `${result['stop_loss']:,.2f}`  (-${result['sl_dollars']:,.0f})\n"
        f"Take Profit: `${result['take_profit']:,.2f}`  (+${result['tp_dollars']:,.0f})\n"
        f"Risk:Reward: `{result['sl_tp_ratio']}`\n\n"
        f"*Indicators (1H)*\n"
        f"RSI: `{result['rsi']}`  |  ATR: `${result['atr']:,.0f}`\n"
        f"MACD Hist: `{result['macd_hist']}`\n"
        f"EMA50: `${result['ema50']:,.0f}`  |  EMA200: `${result['ema200']:,.0f}`\n"
        f"Golden Cross: {'✅' if result['golden_cross'] else '❌'}\n\n"
        f"*Trend:* {'📈 UP' if result['trend_up'] else '📉 DOWN'}  |  Near support: {result['near_support']}"
    )

    if TELEGRAM_CHAT_ID:
        _post_message(TELEGRAM_CHAT_ID, msg)
    if TELEGRAM_CHANNEL_ID and "ENTER" in signal:
        _post_message(TELEGRAM_CHANNEL_ID, msg)

# ==========================
# MAIN
# ==========================
def strategy_cycle():
    """Run CryptoPulse for BTC and ETH."""
    log.info("===== CryptoPulse Check =====")

    # Get Fear & Greed once
    fg_value, fg_label = get_fear_greed()
    btc_dom = get_btc_dominance()

    results = []
    for name, ticker in SYMBOLS.items():
        try:
            df = get_crypto_data(ticker)
            result = evaluate_entry(df, name)
            result["fear_greed"] = (fg_value, fg_label)
            result["btc_dominance"] = btc_dom

            # Chart
            chart_path = generate_chart(df, name)
            result["chart_path"] = chart_path

            log.info(f"  {name}: {result['signal']} | ${result['price']:,.0f} | RSI {result['rsi']}")

            send_telegram(result)
            if chart_path and TELEGRAM_CHAT_ID:
                _send_photo(TELEGRAM_CHAT_ID, chart_path)

            results.append(result)
        except Exception as e:
            log.error(f"  {name} error: {e}")

    return results


if __name__ == "__main__":
    results = strategy_cycle()
    for r in results:
        print(f"\n{r['symbol']}: {r['signal']} | ${r['price']:,.2f} | RSI {r['rsi']}")
