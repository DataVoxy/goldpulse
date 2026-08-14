"""
SilverPulse Strategy
--------------------
Algorithmic silver (XAG/USD) trading signals via Telegram.
Same approach as GoldPulse but tuned for silver's higher volatility.

Ticker: SI=F (Silver futures on Yahoo Finance)
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

SYMBOL               = "SI=F"       # Silver futures
PRODUCT_NAME         = "SilverPulse"
ATR_PERIOD           = 14
RSI_PERIOD           = 14
RSI_OVERSOLD         = 60           # Silver is more volatile, tighter threshold
RSI_OVERBOUGHT       = 40           # Mirror for shorts
MACD_FAST            = 12
MACD_SLOW            = 26
MACD_SIGNAL          = 9

# eToro position settings for silver
LEVERAGE             = 20           # eToro silver CFD leverage
MARGIN_USD           = 200          # Smaller position for silver (cheaper per oz)
EXPOSURE_USD         = MARGIN_USD * LEVERAGE   # = $4,000

# Risk: silver moves ~2x gold in %, so tighter ATR multipliers
SL_ATR_MULT          = 1.0          # Tighter SL (silver is noisy)
TP_ATR_MULT          = 2.0          # Keep 1:2 risk/reward
CHECK_INTERVAL       = 1800         # 30 min (same as GoldPulse)
MAX_TRADE_DURATION_MIN = 240

# Telegram — uses same bot, different channel
TELEGRAM_BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_ID  = os.environ.get("SILVERPULSE_CHANNEL_ID", "")  # separate channel

# Sessions
SESSIONS = {
    "London": (7, 16),
    "US":     (13, 22),
}
CLOSE_BEFORE_HOUR_UTC = 21

# ==========================
# LOGGING
# ==========================
_log_path = str(Path(__file__).parent.parent / "logs" / "silver_strategy.log")
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
# SESSION CHECK
# ==========================
def get_active_session():
    hour_utc = datetime.now(timezone.utc).hour
    active = []
    for name, (start, end) in SESSIONS.items():
        if start <= hour_utc < end:
            active.append(name)
    return active if active else None

# ==========================
# DATA
# ==========================
def get_silver_data():
    """Fetch 1H candles for silver with retry."""
    for attempt in range(3):
        df = yf.download(SYMBOL, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty and len(df) >= MACD_SLOW + MACD_SIGNAL + 5:
            return df
        time.sleep(5)
    raise ValueError("Not enough silver 1H data after 3 attempts")

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
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

def compute_support(df, lookback=20):
    return float(df["Low"].iloc[-lookback:].min())

def compute_resistance(df, lookback=20):
    return float(df["High"].iloc[-lookback:].max())

# ==========================
# GOLD/SILVER RATIO
# ==========================
def get_gold_silver_ratio():
    """Fetch current gold/silver ratio — useful context for silver traders."""
    try:
        gold = yf.download("GC=F", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(gold.columns, pd.MultiIndex):
            gold.columns = gold.columns.get_level_values(0)
        silver = yf.download("SI=F", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(silver.columns, pd.MultiIndex):
            silver.columns = silver.columns.get_level_values(0)

        if gold.empty or silver.empty:
            return None, None

        ratio_now = float(gold["Close"].iloc[-1]) / float(silver["Close"].iloc[-1])
        ratio_avg = float(gold["Close"].rolling(20).mean().iloc[-1]) / float(silver["Close"].rolling(20).mean().iloc[-1]) if len(gold) >= 20 else ratio_now
        return round(ratio_now, 1), round(ratio_avg, 1)
    except Exception:
        return None, None

# ==========================
# ENTRY LOGIC
# ==========================
def evaluate_entry(df):
    """Evaluate silver entry signal."""
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
    last_macd = float(macd_line.iloc[-1])
    last_sig = float(sig.iloc[-1])

    support = compute_support(df)
    resistance = compute_resistance(df)

    # Position sizing
    oz_per_position = EXPOSURE_USD / last_close
    pip_value = oz_per_position  # $1 move = oz * $1

    # SL/TP
    sl_pts = last_atr * SL_ATR_MULT
    tp_pts = last_atr * TP_ATR_MULT
    entry_price = last_close

    # Time check
    hour_utc = datetime.now(timezone.utc).hour
    too_late = hour_utc >= (CLOSE_BEFORE_HOUR_UTC - 2)
    sessions = get_active_session()

    # Trend filter
    ema50 = float(close.ewm(span=50).mean().iloc[-1])
    trend_up = last_close > ema50
    trend_down = last_close < ema50

    # Entry conditions
    near_support = last_close <= support + (last_atr * 6)
    near_resistance = last_close >= resistance - (last_atr * 6)
    rsi_oversold = last_rsi < RSI_OVERSOLD
    rsi_overbought = last_rsi > (100 - RSI_OVERBOUGHT)

    # Default levels
    stop_loss = entry_price - sl_pts
    take_profit = entry_price + tp_pts

    # Signal logic
    if not sessions:
        signal = "WAIT - outside trading session"
    elif too_late:
        signal = "TOO LATE - close before overnight"
    elif trend_up and near_support and rsi_oversold:
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
    actual_ratio = round(tp_dollars / sl_dollars, 1) if sl_dollars > 0 else 0

    # Gold/Silver ratio
    gs_ratio, gs_avg = get_gold_silver_ratio()

    return {
        "signal": signal,
        "product": PRODUCT_NAME,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "price": round(entry_price, 3),
        "support": round(support, 3),
        "resistance": round(resistance, 3),
        "entry": round(entry_price, 3),
        "stop_loss": round(stop_loss, 3),
        "take_profit": round(take_profit, 3),
        "sl_pts": round(sl_pts, 3),
        "tp_pts": round(tp_pts, 3),
        "margin_usd": MARGIN_USD,
        "exposure_usd": EXPOSURE_USD,
        "leverage": f"x{LEVERAGE}",
        "sl_dollars": round(sl_dollars, 2),
        "tp_dollars": round(tp_dollars, 2),
        "sl_tp_ratio": f"1:{actual_ratio} (TP:SL)",
        "rsi": round(last_rsi, 1),
        "macd_hist": round(last_hist, 4),
        "atr": round(last_atr, 3),
        "ema50": round(ema50, 3),
        "near_support": near_support,
        "near_resistance": near_resistance,
        "trend_up": trend_up,
        "trend_down": trend_down,
        "session": sessions,
        "too_late": too_late,
        "gold_silver_ratio": gs_ratio,
        "gold_silver_ratio_avg": gs_avg,
    }

# ==========================
# CHART
# ==========================
_CHART_PATH = Path(__file__).parent.parent / "logs" / "silver_chart.png"

def generate_chart(df):
    """Generate silver price chart (last 72h)."""
    if not HAS_MATPLOTLIB:
        return None
    try:
        os.makedirs(_CHART_PATH.parent, exist_ok=True)
        recent = df.tail(72).copy()
        if len(recent) < 5:
            return None

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(recent.index, recent["Close"], color="#C0C0C0", linewidth=2)
        ax.fill_between(recent.index, recent["Close"], recent["Close"].min() * 0.999, alpha=0.15, color="#C0C0C0")

        support = float(recent["Low"].min())
        resistance = float(recent["High"].max())
        ax.axhline(y=support, color="#ff4444", linestyle="--", alpha=0.7, linewidth=1)
        ax.axhline(y=resistance, color="#44ff44", linestyle="--", alpha=0.7, linewidth=1)
        ax.axhline(y=float(recent["Close"].iloc[-1]), color="white", linestyle=":", alpha=0.4, linewidth=0.8)

        ax.set_facecolor("#1a1a2e")
        fig.patch.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white", labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("white")
        ax.spines["bottom"].set_color("white")
        ax.set_title(f"XAG/USD 1H — ${float(recent['Close'].iloc[-1]):.2f}", color="white", fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:.2f}"))
        plt.xticks(rotation=20)
        plt.tight_layout()
        plt.savefig(_CHART_PATH, dpi=120, bbox_inches="tight")
        plt.close()
        return str(_CHART_PATH)
    except Exception as e:
        log.warning(f"Silver chart failed: {e}")
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
    """Send silver signal to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    signal = result["signal"]
    if "ENTER LONG" in signal:
        emoji = "✅ "
    elif "ENTER SHORT" in signal:
        emoji = "🔴 "
    elif "TOO LATE" in signal:
        emoji = "⏰ "
    else:
        emoji = "⏳ "

    # Gold/Silver ratio context
    gs_line = ""
    if result.get("gold_silver_ratio"):
        gs_ratio = result["gold_silver_ratio"]
        gs_avg = result["gold_silver_ratio_avg"] or gs_ratio
        gs_status = "🔥 Silver cheap" if gs_ratio > gs_avg else "Silver expensive"
        gs_line = f"\n*Gold/Silver Ratio:* `{gs_ratio}` (avg: {gs_avg}) — {gs_status}\n"

    msg = (
        f"⚪ *SilverPulse Alert*\n"
        f"`{result['timestamp']}`\n\n"
        f"*Signal:* {emoji}{signal}\n"
        f"{gs_line}\n"
        f"*Levels*\n"
        f"Entry:       `${result['entry']}`\n"
        f"Stop Loss:   `${result['stop_loss']}`  (-${result['sl_dollars']})\n"
        f"Take Profit: `${result['take_profit']}`  (+${result['tp_dollars']})\n"
        f"SL:TP Ratio: `{result['sl_tp_ratio']}`\n\n"
        f"*Indicators (1H)*\n"
        f"RSI: `{result['rsi']}`  |  ATR: `{result['atr']}`\n"
        f"MACD Hist: `{result['macd_hist']}`  |  EMA50: `${result['ema50']}`\n\n"
        f"*Filters*\n"
        f"Trend: {'📈 UP' if result['trend_up'] else '📉 DOWN'}  |  Session: {result['session']}\n"
        f"Near support: {result['near_support']}  |  Too late: {result['too_late']}"
    )

    if TELEGRAM_CHAT_ID:
        _post_message(TELEGRAM_CHAT_ID, msg)

    if TELEGRAM_CHANNEL_ID and ("ENTER" in signal):
        _post_message(TELEGRAM_CHANNEL_ID, msg)

# ==========================
# MAIN
# ==========================
def strategy_cycle():
    """Run one SilverPulse strategy check."""
    log.info("===== SilverPulse Check =====")
    df = get_silver_data()
    result = evaluate_entry(df)

    chart_path = generate_chart(df)
    result["chart_path"] = chart_path

    log.info(f"Signal: {result['signal']} | Price: ${result['price']} | RSI: {result['rsi']}")
    log.info(f"G/S Ratio: {result.get('gold_silver_ratio', 'N/A')}")

    send_telegram(result)

    if chart_path and TELEGRAM_CHAT_ID:
        _send_photo(TELEGRAM_CHAT_ID, chart_path)

    return result


if __name__ == "__main__":
    # Run once for testing
    result = strategy_cycle()
    print(f"\nSilverPulse: {result['signal']}")
    print(f"Price: ${result['price']} | RSI: {result['rsi']} | ATR: {result['atr']}")
    print(f"Gold/Silver Ratio: {result.get('gold_silver_ratio', 'N/A')}")
