import yfinance as yf
import pandas as pd
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ==========================
# CONFIG
# ==========================
_env_path = Path(__file__).parent.parent / ".env"  # GoldPulse root
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
else:
    # Fallback: try current working directory
    _cwd_env = Path(os.getcwd()) / ".env"
    if _cwd_env.exists():
        for _line in _cwd_env.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SYMBOL               = "GC=F"       # Gold futures
ATR_PERIOD           = 14
RSI_PERIOD           = 14
RSI_OVERSOLD         = 65           # Relaxed for 1H — current RSI around 55-59
MACD_FAST            = 12
MACD_SLOW            = 26
MACD_SIGNAL          = 9

# eToro position settings
LEVERAGE             = 20           # eToro gold CFD leverage
MARGIN_USD           = 400          # Amount you put in ($)
EXPOSURE_USD         = MARGIN_USD * LEVERAGE   # = $8,000
TROY_OZ_PER_USD      = None         # Calculated dynamically from live price

# Risk ratio: ~1:1.7 in your favor (TP > SL)
# Based on ATR — SL = 1.2x ATR, TP = 2.0x ATR
# Wider SL to avoid noise stops, bigger TP for meaningful wins
SL_ATR_MULT          = 1.2
TP_ATR_MULT          = 2.0           # gives ~1:1.7 SL:TP ratio
CHECK_INTERVAL       = 1800         # Seconds between checks (30 minutes)
MAX_TRADE_DURATION_MIN = 240        # Force-close after 4 hours if no TP/SL hit

# Telegram
TELEGRAM_BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")      # your personal chat
TELEGRAM_CHANNEL_ID  = os.environ.get("TELEGRAM_CHANNEL_ID", "")   # public channel

# Sessions in UTC — trade London + US, avoid Asia
SESSIONS = {
    "London": (7, 16),   # 07:00–16:00 UTC
    "US":     (13, 22),  # 13:00–22:00 UTC
}
CLOSE_BEFORE_HOUR_UTC = 21          # Close all before 21:00 UTC to avoid overnight fee

# High-impact events to avoid
HIGH_IMPACT_KEYWORDS = [
    "nfp", "non-farm", "fomc", "fed rate", "ecb rate",
    "cpi", "gdp", "interest rate decision"
]

# ==========================
# LOGGING
# ==========================
_log_path = str(Path(__file__).parent.parent / "logs" / "strategy.log")
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
    """Returns active session name or None if outside trading hours."""
    hour_utc = datetime.now(timezone.utc).hour
    active = []
    for name, (start, end) in SESSIONS.items():
        if start <= hour_utc < end:
            active.append(name)
    return active if active else None

# ==========================
# DATA
# ==========================
def get_4h_data():
    """Fetch 1H candles for gold with retry."""
    for attempt in range(3):
        df = yf.download(SYMBOL, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty and len(df) >= MACD_SLOW + MACD_SIGNAL + 5:
            return df
        time.sleep(5)  # wait 5 seconds before retry
    raise ValueError("Not enough 1H data after 3 attempts")

# ==========================
# INDICATORS
# ==========================
def compute_rsi(series, period=RSI_PERIOD):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_macd(series):
    ema_fast   = series.ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow   = series.ewm(span=MACD_SLOW,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal     = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram  = macd_line - signal
    return macd_line, signal, histogram

def compute_atr(high, low, close, period=ATR_PERIOD):
    high_low   = high - low
    high_close = (high - close.shift()).abs()
    low_close  = (low  - close.shift()).abs()
    tr         = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

def compute_pivot_support(df, lookback=20):
    """Simple support: lowest low of last N candles."""
    return float(df["Low"].iloc[-lookback:].min())

def compute_pivot_resistance(df, lookback=20):
    """Simple resistance: highest high of last N candles."""
    return float(df["High"].iloc[-lookback:].max())

def compute_buy_zones(df, atr):
    """Calculate key buy zone levels based on support and ATR."""
    support = float(df["Low"].iloc[-20:].min())
    current = float(df["Close"].iloc[-1])

    # Buy zones: levels where price might bounce
    zones = []
    # Zone 1: current support
    zones.append(("Support", round(support, 2)))
    # Zone 2: 1x ATR below current price (pullback entry)
    zones.append(("Pullback (1x ATR)", round(current - atr, 2)))
    # Zone 3: 2x ATR below current (deep pullback)
    zones.append(("Deep pullback (2x ATR)", round(current - atr * 2, 2)))

    # Sort by price descending (closest first)
    zones.sort(key=lambda x: x[1], reverse=True)
    return zones

# ==========================
# HIGH IMPACT NEWS CHECK
# ==========================
def has_high_impact_event_soon():
    """
    Scrape ForexFactory and check if a high-impact USD/EUR event
    is within the next 2 hours. Returns (bool, event_name).
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        url     = "https://www.forexfactory.com/calendar"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=10)
        soup    = BeautifulSoup(resp.text, "html.parser")
        rows    = soup.select("tr.calendar__row")

        for row in rows:
            currency = row.select_one("td.calendar__currency")
            title    = row.select_one("td.calendar__event span")
            impact   = row.select_one("td.calendar__impact span")
            time_cell = row.select_one("td.calendar__time")

            if not (currency and title and impact):
                continue

            impact_classes = impact.get("class", [])
            is_high = any("high" in c for c in impact_classes)
            if not is_high:
                continue

            ccy   = currency.get_text(strip=True)
            event = title.get_text(strip=True).lower()
            if ccy not in ("USD", "EUR"):
                continue

            for kw in HIGH_IMPACT_KEYWORDS:
                if kw in event:
                    return True, title.get_text(strip=True)

        return False, None
    except Exception as e:
        log.warning(f"ForexFactory check failed: {e}")
        return False, None  # don't block trading on scrape failure

# ==========================
# ENTRY LOGIC
# ==========================
def evaluate_entry(df):
    """
    Returns a dict with signal and all levels in eToro CFD terms.
    Entry conditions (ALL must be true):
      1. Price near support (within 1x ATR)
      2. RSI < RSI_OVERSOLD (oversold / pullback)
      3. MACD histogram turning positive (momentum shift up)
      4. No high-impact news within ~2h
      5. Active London or US session
      6. Not past close-before time (avoid overnight fee)
    """
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    rsi                    = compute_rsi(close)
    macd_line, sig, hist   = compute_macd(close)
    atr                    = compute_atr(high, low, close)

    last_close   = float(close.iloc[-1])
    last_rsi     = float(rsi.iloc[-1])
    last_hist    = float(hist.iloc[-1])
    prev_hist    = float(hist.iloc[-2])
    last_atr     = float(atr.iloc[-1])
    last_macd    = float(macd_line.iloc[-1])
    last_sig     = float(sig.iloc[-1])

    support      = compute_pivot_support(df)
    resistance   = compute_pivot_resistance(df)

    # eToro position sizing
    troy_oz      = EXPOSURE_USD / last_close   # how many oz your $8000 buys
    pip_value    = troy_oz                     # $1 move in gold = troy_oz dollars P&L

    # SL/TP in price points (ATR based, 1:2 ratio)
    sl_pts       = last_atr * SL_ATR_MULT
    tp_pts       = last_atr * TP_ATR_MULT

    entry_price  = last_close

    # Dollar P&L
    troy_oz      = EXPOSURE_USD / last_close
    pip_value    = troy_oz

    # Time check — don't enter if close to overnight cutoff (2h buffer)
    hour_utc         = datetime.now(timezone.utc).hour
    too_late         = hour_utc >= (CLOSE_BEFORE_HOUR_UTC - 2)  # No new trades after 19:00 UTC

    # Session check
    sessions = get_active_session()

    # Trend filter — EMA50
    ema50 = float(close.ewm(span=50).mean().iloc[-1])
    trend_up = last_close > ema50
    trend_down = last_close < ema50

    # Entry conditions
    near_support     = last_close <= support + (last_atr * 8)  # Wider zone for 1H
    near_resistance  = last_close >= resistance - (last_atr * 8)
    rsi_oversold     = last_rsi < RSI_OVERSOLD
    rsi_overbought   = last_rsi > (100 - RSI_OVERSOLD)  # Mirror: RSI > 35

    # News check
    news_block, news_event = has_high_impact_event_soon()

    # Final signal — trade WITH the trend
    if not sessions:
        signal = "WAIT - outside trading session (avoid Asia)"
    elif too_late:
        signal = "TOO LATE - close before overnight fee"
    elif news_block:
        signal = f"WAIT - high impact news: {news_event}"
    elif trend_up and near_support and rsi_oversold:
        signal = "ENTER LONG"
        stop_loss = entry_price - sl_pts
        take_profit = entry_price + tp_pts
    elif trend_down and near_resistance and rsi_overbought:
        signal = "ENTER SHORT"
        stop_loss = entry_price + sl_pts  # SL above for short
        take_profit = entry_price - tp_pts  # TP below for short
    else:
        signal = "NO TRADE - conditions not met"
        stop_loss = entry_price - sl_pts  # default for display
        take_profit = entry_price + tp_pts

    # Dollar P&L calculations
    sl_dollars = sl_pts * pip_value
    tp_dollars = tp_pts * pip_value
    actual_ratio = round(tp_dollars / sl_dollars, 1) if sl_dollars > 0 else 0

    return {
        "signal":         signal,
        "timestamp":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        # Price levels
        "price":          round(entry_price, 2),
        "support":        round(support, 2),
        "resistance":     round(resistance, 2),
        "entry":          round(entry_price, 2),
        "stop_loss":      round(stop_loss, 2),
        "take_profit":    round(take_profit, 2),
        "sl_pts":         round(sl_pts, 2),
        "tp_pts":         round(tp_pts, 2),
        # eToro dollar terms
        "margin_usd":     MARGIN_USD,
        "exposure_usd":   EXPOSURE_USD,
        "leverage":       f"x{LEVERAGE}",
        "troy_oz":        round(troy_oz, 2),
        "sl_dollars":     round(sl_dollars, 2),
        "tp_dollars":     round(tp_dollars, 2),
        "sl_tp_ratio":    f"1:{actual_ratio}  (TP:SL)",
        # Indicators
        "rsi":            round(last_rsi, 1),
        "macd":           round(last_macd, 4),
        "macd_signal":    round(last_sig, 4),
        "macd_hist":      round(last_hist, 4),
        "atr":            round(last_atr, 2),
        "ema50":          round(ema50, 2),
        # Conditions
        "near_support":   near_support,
        "near_resistance": near_resistance,
        "rsi_oversold":   rsi_oversold,
        "rsi_overbought": rsi_overbought,
        "trend_up":       trend_up,
        "trend_down":     trend_down,
        "session":        sessions,
        "news_block":     news_block,
        "too_late":       too_late,
        "buy_zones":      compute_buy_zones(df, last_atr),
    }

# ==========================
# CHART GENERATION
# ==========================
_CHART_PATH = Path(__file__).parent.parent / "logs" / "gold_chart.png"

def generate_chart(df):
    """Generate a gold price chart image (last 72h)."""
    if not HAS_MATPLOTLIB:
        return None

    try:
        os.makedirs(_CHART_PATH.parent, exist_ok=True)
        # Last 72 candles (72h on 1H chart) for better price action
        recent = df.tail(72).copy()

        if len(recent) < 5:
            return None

        # Skip chart if all prices are the same (stale data)
        if recent["Close"].nunique() <= 1:
            return None

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(recent.index, recent["Close"], color="#FFD700", linewidth=2)
        ax.fill_between(recent.index, recent["Close"], recent["Close"].min() * 0.999, alpha=0.15, color="#FFD700")

        # Support/resistance lines
        support = float(recent["Low"].min())
        resistance = float(recent["High"].max())
        ax.axhline(y=support, color="#ff4444", linestyle="--", alpha=0.7, linewidth=1, label=f"Support ${support:.0f}")
        ax.axhline(y=resistance, color="#44ff44", linestyle="--", alpha=0.7, linewidth=1, label=f"Resistance ${resistance:.0f}")

        # Current price marker
        ax.axhline(y=float(recent["Close"].iloc[-1]), color="white", linestyle=":", alpha=0.4, linewidth=0.8)

        ax.set_facecolor("#1a1a2e")
        fig.patch.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white", labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("white")
        ax.spines["bottom"].set_color("white")
        ax.set_title(f"XAU/USD 1H — ${float(recent['Close'].iloc[-1]):.2f}", color="white", fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:.0f}"))
        ax.legend(loc="upper left", fontsize=7, facecolor="#1a1a2e", edgecolor="white", labelcolor="white")
        plt.xticks(rotation=20)
        plt.tight_layout()
        plt.savefig(_CHART_PATH, dpi=120, bbox_inches="tight")
        plt.close()
        return str(_CHART_PATH)
    except Exception as e:
        log.warning(f"Chart generation failed: {e}")
        return None


def _send_photo(chat_id, photo_path, caption=""):
    """Send a photo to Telegram."""
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as photo:
            resp = req.post(url, data={"chat_id": chat_id, "caption": caption}, files={"photo": photo}, timeout=15)
        if resp.status_code == 200:
            log.info(f"Chart sent to {chat_id}")
        else:
            log.warning(f"Chart send failed for {chat_id}: {resp.status_code}")
    except Exception as e:
        log.warning(f"Chart send error for {chat_id}: {e}")


def compute_daily_trend(df):
    """Compute overall daily trend direction and strength."""
    # Use EMA20 vs EMA50 + price position for trend
    recent = df.tail(24)
    if len(recent) < 20:
        return "Unknown", 0

    close_now = float(recent["Close"].iloc[-1])
    close_24h_ago = float(recent["Close"].iloc[0])
    change_pct = ((close_now - close_24h_ago) / close_24h_ago) * 100

    ema20 = float(recent["Close"].ewm(span=20).mean().iloc[-1])
    ema50 = float(df.tail(50)["Close"].ewm(span=50).mean().iloc[-1]) if len(df) >= 50 else ema20

    # Strength: 1-5 based on change % and EMA alignment
    if change_pct > 1.0 and ema20 > ema50:
        trend, strength = "Strong Bullish", 5
    elif change_pct > 0.3 and ema20 > ema50:
        trend, strength = "Bullish", 4
    elif change_pct > 0.1:
        trend, strength = "Slightly Bullish", 3
    elif change_pct < -1.0 and ema20 < ema50:
        trend, strength = "Strong Bearish", 5
    elif change_pct < -0.3 and ema20 < ema50:
        trend, strength = "Bearish", 4
    elif change_pct < -0.1:
        trend, strength = "Slightly Bearish", 3
    else:
        trend, strength = "Ranging", 2

    return trend, strength


# ==========================
# TELEGRAM NOTIFICATION
# ==========================
def _post_message(chat_id, msg):
    """Send a single Telegram message to one chat_id."""
    try:
        import requests as req
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        log.info(f"Attempting Telegram send to {chat_id}, token present: {bool(TELEGRAM_BOT_TOKEN)}")
        resp = req.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            log.info(f"Telegram message sent to {chat_id}")
        else:
            log.warning(f"Telegram send failed for {chat_id}: {resp.status_code} {resp.text}")
    except Exception as e:
        log.error(f"Telegram error for {chat_id}: {e}", exc_info=True)


def send_telegram(result):
    """Send signal alert via Telegram bot to personal chat and/or public channel."""
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram not configured — skipping notification.")
        return

    signal = result["signal"]

    # === FULL DETAILED MESSAGE (personal chat only) ===
    if "ENTER LONG" in signal:
        display_signal = "✅ " + signal
    elif "TOO LATE" in signal:
        display_signal = "⏰ " + signal
    elif "WAIT - high" in signal:
        display_signal = "🚫 " + signal
    elif "outside trading" in signal:
        display_signal = "💤 " + signal
    else:
        display_signal = "⏳ " + signal

    full_msg = (
        f"🟡 *GoldPulse Alert*\n"
        f"`{result['timestamp']}`\n\n"
        f"*Signal:* {display_signal}\n\n"
        f"*📈 Daily Trend:* {result.get('trend', 'N/A')}  `[{'█' * result.get('trend_strength', 0)}{'░' * (5 - result.get('trend_strength', 0))}]`\n\n"
        f"*Levels*\n"
        f"Entry:       `${result['entry']}`\n"
        f"Stop Loss:   `${result['stop_loss']}`  (-${result['sl_dollars']})\n"
        f"Take Profit: `${result['take_profit']}`  (+${result['tp_dollars']})\n"
        f"SL:TP Ratio: `{result['sl_tp_ratio']}`\n\n"
        f"*Indicators (1H)*\n"
        f"RSI: `{result['rsi']}`  |  ATR: `{result['atr']}`\n"
        f"MACD Hist: `{result['macd_hist']}`\n\n"
        f"*Fundamentals*\n"
        f"DXY: `{result.get('dxy', 'N/A')}` (avg: {result.get('dxy_avg', 'N/A')})  {'📉 weak' if result.get('dxy') and result.get('dxy_avg') and result['dxy'] < result['dxy_avg'] else '📈 strong'}\n"
        f"US 10Y: `{result.get('us10y', 'N/A')}%` (avg: {result.get('us10y_avg', 'N/A')}%)  {'📉 falling' if result.get('us10y') and result.get('us10y_avg') and result['us10y'] < result['us10y_avg'] else '📈 rising'}\n\n"
        f"*🎯 Buy Zones*\n"
        f"{chr(10).join(['• ' + name + ': `$' + str(price) + '`' for name, price in result.get('buy_zones', [])]) or '• N/A'}\n\n"
        f"*Filters*\n"
        f"Session: {result['session']}  |  Near support: {result['near_support']}\n"
        f"News block: {result['news_block']}  |  Too late: {result['too_late']}"
    )

    # Personal chat always gets full detailed message
    if TELEGRAM_CHAT_ID:
        _post_message(TELEGRAM_CHAT_ID, full_msg)
        if result.get("chart_path"):
            _send_photo(TELEGRAM_CHAT_ID, result["chart_path"])

    # === CHANNEL MESSAGES ===
    _last_channel_full_file = Path(__file__).parent.parent / "logs" / ".last_channel_full"

    if "ENTER LONG" in signal or "ENTER SHORT" in signal:
        # Only send signal to channel if we actually opened a new trade
        if result.get("trade_opened", False):
            if "LONG" in signal:
                direction_emoji = "🟢"
                direction_text = "BUY"
            else:
                direction_emoji = "🔴"
                direction_text = "SELL"

            channel_msg = (
                f"🚨{direction_emoji} *GOLD {direction_text} NOW*\n\n"
                f"Entry: `${result['entry']}`\n"
                f"SL: `${result['stop_loss']}`\n"
                f"TP: `${result['take_profit']}`\n\n"
                f"Trend: {'📈 UP' if result.get('trend_up') else '📉 DOWN'}\n"
                f"RSI: {result['rsi']}  |  DXY: {'weak 📉' if result.get('dxy') and result.get('dxy_avg') and result['dxy'] < result['dxy_avg'] else 'strong 📈'}\n\n"
                f"⚡ Manage your risk. This is not financial advice."
            )
            if TELEGRAM_CHANNEL_ID:
                _post_message(TELEGRAM_CHANNEL_ID, channel_msg)
                if result.get("chart_path"):
                    _send_photo(TELEGRAM_CHANNEL_ID, result["chart_path"])
                _last_channel_full_file.parent.mkdir(parents=True, exist_ok=True)
                _last_channel_full_file.write_text(str(datetime.now(timezone.utc).timestamp()))

    else:
        # HOURLY MARKET UPDATE (no news/events, just indicators + levels)
        try:
            if _last_channel_full_file.exists():
                last_ts = float(_last_channel_full_file.read_text().strip())
                elapsed = datetime.now(timezone.utc).timestamp() - last_ts
                send_hourly = elapsed >= 3600  # 1 hour
            else:
                send_hourly = True
        except Exception:
            send_hourly = True

        if TELEGRAM_CHANNEL_ID and send_hourly:
            hourly_msg = (
                f"📊 *Hourly Update*\n"
                f"`{result['timestamp']}`\n\n"
                f"Gold: `${result['entry']}`\n"
                f"Trend: {result.get('trend', 'N/A')}  `[{'█' * result.get('trend_strength', 0)}{'░' * (5 - result.get('trend_strength', 0))}]`\n\n"
                f"RSI: `{result['rsi']}`  |  ATR: `{result['atr']}`\n"
                f"DXY: `{result.get('dxy', 'N/A')}`  {'📉 weak' if result.get('dxy') and result.get('dxy_avg') and result['dxy'] < result['dxy_avg'] else '📈 strong'}\n"
                f"US 10Y: `{result.get('us10y', 'N/A')}%`  {'📉 falling' if result.get('us10y') and result.get('us10y_avg') and result['us10y'] < result['us10y_avg'] else '📈 rising'}\n\n"
                f"*🎯 Buy Zones*\n"
                f"{chr(10).join(['• ' + name + ': `$' + str(price) + '`' for name, price in result.get('buy_zones', [])]) or '• N/A'}\n\n"
                f"_No setup yet — watching for entry_"
            )
            _post_message(TELEGRAM_CHANNEL_ID, hourly_msg)
            if result.get("chart_path"):
                _send_photo(TELEGRAM_CHANNEL_ID, result["chart_path"])
            _last_channel_full_file.parent.mkdir(parents=True, exist_ok=True)
            _last_channel_full_file.write_text(str(datetime.now(timezone.utc).timestamp()))


# ==========================
# MAIN CYCLE
# ==========================
def strategy_cycle():
    log.info("===== Strategy Check =====")
    df     = get_4h_data()
    result = evaluate_entry(df)

    # Compute daily trend
    trend, strength = compute_daily_trend(df)
    result["trend"] = trend
    result["trend_strength"] = strength
    trend_bar = "█" * strength + "░" * (5 - strength)

    # Generate chart
    chart_path = generate_chart(df)
    result["chart_path"] = chart_path

    # Fetch fundamentals for context (not used in entry decision)
    try:
        dxy_df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(dxy_df.columns, pd.MultiIndex):
            dxy_df.columns = dxy_df.columns.get_level_values(0)
        dxy_now = round(float(dxy_df["Close"].iloc[-1]), 2)
        dxy_avg = round(float(dxy_df["Close"].rolling(20).mean().iloc[-1]), 2) if len(dxy_df) >= 20 else dxy_now
    except Exception:
        dxy_now, dxy_avg = None, None

    try:
        tnx_df = yf.download("^TNX", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(tnx_df.columns, pd.MultiIndex):
            tnx_df.columns = tnx_df.columns.get_level_values(0)
        us10y_now = round(float(tnx_df["Close"].iloc[-1]), 2)
        us10y_avg = round(float(tnx_df["Close"].rolling(20).mean().iloc[-1]), 2) if len(tnx_df) >= 20 else us10y_now
    except Exception:
        us10y_now, us10y_avg = None, None

    result["dxy"] = dxy_now
    result["dxy_avg"] = dxy_avg
    result["us10y"] = us10y_now
    result["us10y_avg"] = us10y_avg

    # Fetch gold/macro news headlines
    try:
        from core.signals import get_gold_news, get_macro_calendar
    except ImportError:
        try:
            from signals import get_gold_news, get_macro_calendar
        except ImportError:
            get_gold_news = None
            get_macro_calendar = None

    headlines = []
    if get_gold_news:
        try:
            headlines = get_gold_news()[:3]  # top 3 headlines
        except Exception:
            headlines = []

    macro_events = []
    if get_macro_calendar:
        try:
            events = get_macro_calendar()
            # Sort by impact: high first, then medium, then low
            impact_order = {"high": 0, "medium": 1, "low": 2}
            sorted_events = sorted(
                [e for e in events if isinstance(e, dict) and "event" in e],
                key=lambda e: impact_order.get(e.get("impact", "low"), 2)
            )
            for e in sorted_events:
                if e.get("impact") == "high":
                    e["rating"] = "⚠️ 5/5"
                elif e.get("impact") == "medium":
                    e["rating"] = "🔶 3/5"
                else:
                    e["rating"] = "🔹 1/5"
                macro_events.append(e)
                if len(macro_events) >= 5:
                    break
        except Exception:
            macro_events = []

    result["headlines"] = headlines
    result["macro_events"] = macro_events

    log.info(f"Time        : {result['timestamp']}")
    log.info(f"Signal      : {result['signal']}")
    log.info("--- eToro Position ---")
    log.info(f"Amount      : ${result['margin_usd']}  |  Exposure: ${result['exposure_usd']}  |  Leverage: {result['leverage']}")
    log.info(f"Troy oz     : {result['troy_oz']}")
    log.info(f"Gold price  : ${result['price']}")
    log.info("--- Levels ---")
    log.info(f"Entry       : ${result['entry']}")
    log.info(f"Stop Loss   : ${result['stop_loss']}  ({result['sl_pts']} pts)  -> -${result['sl_dollars']}")
    log.info(f"Take Profit : ${result['take_profit']}  ({result['tp_pts']} pts)  -> +${result['tp_dollars']}")
    log.info(f"SL:TP Ratio : {result['sl_tp_ratio']}")
    log.info("--- Support/Resistance ---")
    log.info(f"Support     : ${result['support']}  |  Resistance: ${result['resistance']}")
    log.info("--- Indicators ---")
    log.info(f"RSI (1H)    : {result['rsi']}  (oversold: {result['rsi_oversold']} | overbought: {result['rsi_overbought']})")
    log.info(f"MACD        : {result['macd']}  Signal: {result['macd_signal']}  Hist: {result['macd_hist']}")
    log.info(f"ATR (1H)    : {result['atr']}  |  EMA50: ${result['ema50']}")
    log.info(f"Trend       : {'UP' if result['trend_up'] else 'DOWN'}")
    log.info("--- Filters ---")
    log.info(f"Session     : {result['session']}  |  Too late: {result['too_late']}")
    log.info(f"News block  : {result['news_block']}")
    log.info(f"Near support: {result['near_support']}")
    log.info("==========================\n")

    # Track trades: open new trade on ENTER LONG/SHORT (only if no open trade exists)
    trade_opened = False
    if "ENTER LONG" in result["signal"] or "ENTER SHORT" in result["signal"]:
        existing = _load_open_trades()
        if not existing:
            open_trade(result)
            trade_opened = True
        else:
            log.info(f"Skipping new trade — already {len(existing)} open position(s)")

    result["trade_opened"] = trade_opened

    # Check if any open trades hit TP or SL
    closed_trades = check_open_trades(result["price"])
    for row in closed_trades:
        send_trade_result(row)

    send_telegram(result)

    # Check if it's time for a daily report
    check_daily_report()

    # Update public dashboard data
    try:
        import subprocess
        dash_script = Path(__file__).parent.parent / "landing" / "generate_dashboard_data.py"
        if dash_script.exists():
            subprocess.run(["py", str(dash_script)], capture_output=True, timeout=30)
    except Exception as e:
        log.warning(f"Dashboard data update failed: {e}")

    return result


# ==========================
# DAILY REPORT
# ==========================
_REPORT_SENT_FILE = Path(__file__).parent.parent / "logs" / ".report_sent"

def _already_sent_report(label):
    """Check if a report was already sent for this label today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{today}_{label}"
    if _REPORT_SENT_FILE.exists():
        sent = _REPORT_SENT_FILE.read_text().strip().splitlines()
        if key in sent:
            return True
    return False

def _mark_report_sent(label):
    """Mark a report as sent for today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{today}_{label}"
    os.makedirs(_REPORT_SENT_FILE.parent, exist_ok=True)
    with open(_REPORT_SENT_FILE, "a") as f:
        f.write(key + "\n")

def generate_daily_report(report_type="midday"):
    """
    Generate a daily market recap.
    report_type: 'midday' (noon) or 'evening' (6pm)
    """
    log.info(f"===== Daily Report ({report_type}) =====")

    try:
        # Gold data - today's action
        df = yf.download("GC=F", period="5d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty:
            log.warning("No gold data for daily report")
            return

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_data = df[df.index.strftime("%Y-%m-%d") == today_str]

        if today_data.empty:
            # Use last trading day
            today_data = df.tail(24)  # last 24 hours

        gold_open = float(today_data["Open"].iloc[0])
        gold_now = float(df["Close"].iloc[-1])
        gold_high = float(today_data["High"].max())
        gold_low = float(today_data["Low"].min())
        gold_change = gold_now - gold_open
        gold_change_pct = (gold_change / gold_open) * 100
        gold_range = gold_high - gold_low

        # DXY
        try:
            dxy_df = yf.download("DX-Y.NYB", period="2d", interval="1h", progress=False, auto_adjust=True)
            if isinstance(dxy_df.columns, pd.MultiIndex):
                dxy_df.columns = dxy_df.columns.get_level_values(0)
            dxy_now = round(float(dxy_df["Close"].iloc[-1]), 2)
            dxy_avg = round(float(dxy_df["Close"].rolling(20).mean().iloc[-1]), 2) if len(dxy_df) >= 20 else dxy_now
        except Exception:
            dxy_now, dxy_avg = None, None

        # VIX
        try:
            vix_df = yf.download("^VIX", period="2d", interval="1h", progress=False, auto_adjust=True)
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = vix_df.columns.get_level_values(0)
            vix_now = round(float(vix_df["Close"].iloc[-1]), 2)
        except Exception:
            vix_now = None

        # US 10Y
        try:
            tnx_df = yf.download("^TNX", period="2d", interval="1h", progress=False, auto_adjust=True)
            if isinstance(tnx_df.columns, pd.MultiIndex):
                tnx_df.columns = tnx_df.columns.get_level_values(0)
            us10y = round(float(tnx_df["Close"].iloc[-1]), 2)
        except Exception:
            us10y = None

        # Silver for gold/silver ratio
        try:
            silver_df = yf.download("SI=F", period="2d", interval="1h", progress=False, auto_adjust=True)
            if isinstance(silver_df.columns, pd.MultiIndex):
                silver_df.columns = silver_df.columns.get_level_values(0)
            silver_now = float(silver_df["Close"].iloc[-1])
            gs_ratio = round(gold_now / silver_now, 1)
        except Exception:
            silver_now, gs_ratio = None, None

        # News
        try:
            from core.signals import get_gold_news
        except ImportError:
            try:
                from signals import get_gold_news
            except ImportError:
                get_gold_news = None

        headlines = []
        if get_gold_news:
            try:
                headlines = get_gold_news()[:4]
            except Exception:
                pass

        # Determine mood
        if gold_change_pct > 0.5:
            mood = "🟢 Bullish"
        elif gold_change_pct < -0.5:
            mood = "🔴 Bearish"
        else:
            mood = "🟡 Flat"

        # Generate prediction based on technicals + macro
        rsi_series = df["Close"].diff()
        rsi_gain = rsi_series.clip(lower=0).ewm(com=13, min_periods=14).mean()
        rsi_loss = (-rsi_series.clip(upper=0)).ewm(com=13, min_periods=14).mean()
        rsi_val = float((100 - (100 / (1 + rsi_gain / rsi_loss))).iloc[-1])

        ema20 = float(df["Close"].ewm(span=20).mean().iloc[-1])
        ema50 = float(df["Close"].ewm(span=50).mean().iloc[-1])

        # Prediction logic
        bull_signals = 0
        bear_signals = 0
        watch_items = []

        if ema20 > ema50:
            bull_signals += 1
        else:
            bear_signals += 1

        if rsi_val < 40:
            bull_signals += 1  # oversold = bounce likely
            watch_items.append("RSI oversold — watch for bounce")
        elif rsi_val > 60:
            bear_signals += 1  # overbought = pullback likely
            watch_items.append("RSI overbought — pullback possible")

        if gold_change_pct > 0.3:
            bull_signals += 1
        elif gold_change_pct < -0.3:
            bear_signals += 1

        if dxy_now and dxy_avg and dxy_now < dxy_avg:
            bull_signals += 1  # weak dollar = gold bullish
            watch_items.append("DXY below average — supportive for gold")
        elif dxy_now and dxy_avg and dxy_now > dxy_avg:
            bear_signals += 1
            watch_items.append("DXY above average — headwind for gold")

        if vix_now and vix_now > 20:
            bull_signals += 1  # fear = gold safe haven
            watch_items.append("VIX elevated — safe haven demand possible")

        if bull_signals > bear_signals + 1:
            prediction = "🟢 Bullish bias for afternoon"
        elif bear_signals > bull_signals + 1:
            prediction = "🔴 Bearish bias for afternoon"
        else:
            prediction = "🟡 Sideways / Choppy expected"

        # Default watch items if none generated
        if not watch_items:
            watch_items.append("US session open (13:30 UTC) — volume spike expected")
        watch_items.append("Support at $" + f"{gold_low:.0f}" + " | Resistance at $" + f"{gold_high:.0f}")

        # Build message
        if report_type == "midday":
            header = "📊 *GoldPulse Midday Recap*"
            time_label = "Morning session"
        else:
            header = "📊 *GoldPulse Evening Recap*"
            time_label = "Full day"

        msg = (
            f"{header}\n"
            f"`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
            f"*{time_label} — {mood}*\n\n"
            f"*Gold (XAU/USD)*\n"
            f"Price: `${gold_now:.2f}`  ({'+' if gold_change >= 0 else ''}{gold_change:.2f} / {'+' if gold_change_pct >= 0 else ''}{gold_change_pct:.2f}%)\n"
            f"High: `${gold_high:.2f}`  |  Low: `${gold_low:.2f}`\n"
            f"Range: `${gold_range:.2f}`\n\n"
            f"*Macro*\n"
            f"DXY: `{dxy_now}`  |  VIX: `{vix_now}`\n"
            f"US 10Y: `{us10y}%`  |  Au/Ag ratio: `{gs_ratio}`\n\n"
            f"*🔮 Afternoon Outlook*\n"
            f"{prediction}\n\n"
            f"*👀 What to watch*\n"
            f"{chr(10).join(['• ' + w for w in watch_items[:4]])}\n\n"
            f"*🎯 Buy Zones*\n"
            f"• Support: `${gold_low:.2f}`\n"
            f"• Pullback entry: `${gold_now - gold_range:.2f}`\n"
            f"• Deep pullback: `${gold_now - gold_range * 2:.2f}`\n\n"
            f"*📰 Headlines*\n"
            f"{chr(10).join(['• ' + h for h in headlines[:4]]) or '• No headlines'}\n\n"
            f"_Follow @goldpulse14 for live signals_"
        )

        # Send to both personal and channel
        if TELEGRAM_CHAT_ID:
            _post_message(TELEGRAM_CHAT_ID, msg)
        if TELEGRAM_CHANNEL_ID:
            _post_message(TELEGRAM_CHANNEL_ID, msg)

        _mark_report_sent(report_type)
        log.info(f"Daily report ({report_type}) sent.")

    except Exception as e:
        log.error(f"Daily report error: {e}", exc_info=True)


def check_daily_report():
    """Check if it's time to send a daily report (noon or 6pm Amsterdam time)."""
    from datetime import timedelta

    # Amsterdam is UTC+1 (winter) or UTC+2 (summer/CEST)
    # July = CEST = UTC+2
    utc_now = datetime.now(timezone.utc)
    amsterdam_hour = (utc_now.hour + 2) % 24  # CEST offset

    # Send midday report at noon (12:00 Amsterdam = 10:00 UTC)
    if amsterdam_hour == 12 and not _already_sent_report("midday"):
        generate_daily_report("midday")

    # Send evening report at 6pm (18:00 Amsterdam = 16:00 UTC)
    elif amsterdam_hour == 18 and not _already_sent_report("evening"):
        generate_daily_report("evening")

# ==========================
# TRADE TRACKING
# ==========================
import json

_TRADES_FILE = Path(__file__).parent.parent / "logs" / "trades.json"

def _load_open_trades():
    """Load open trades from JSON file."""
    if _TRADES_FILE.exists():
        try:
            return json.loads(_TRADES_FILE.read_text())
        except Exception:
            return []
    return []

def _save_open_trades(trades):
    """Save open trades to JSON file."""
    os.makedirs(_TRADES_FILE.parent, exist_ok=True)
    _TRADES_FILE.write_text(json.dumps(trades, indent=2))

_RESULTS_FILE = Path(__file__).parent.parent / "logs" / "trade_results.csv"

def _log_trade_result(trade, outcome, exit_price):
    """Log a completed trade to CSV (includes spread + slippage costs)."""
    import csv
    os.makedirs(_RESULTS_FILE.parent, exist_ok=True)
    file_exists = _RESULTS_FILE.exists()

    fields = ["open_time", "close_time", "entry", "stop_loss", "take_profit", "exit_price",
              "outcome", "pnl_pts", "pnl_usd", "costs", "rsi", "atr", "duration_min"]

    entry = trade["entry"]
    direction = trade.get("direction", "long")
    if direction == "short":
        pnl_pts = entry - exit_price  # Short: profit when price goes down
    else:
        pnl_pts = exit_price - entry  # Long: profit when price goes up
    troy_oz = EXPOSURE_USD / entry
    pnl_usd_raw = pnl_pts * troy_oz

    # Deduct realistic trading costs (eToro gold CFD)
    # Spread: ~$0.50 per side × troy_oz (open + close = 2 sides)
    spread_cost = 0.50 * troy_oz * 2  # ~$1.95 for $8000 exposure
    # Slippage estimate: ~$0.20 per side
    slippage_cost = 0.20 * troy_oz * 2  # ~$0.78
    total_costs = round(spread_cost + slippage_cost, 2)

    pnl_usd = round(pnl_usd_raw - total_costs, 2)
    close_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    duration = round((datetime.now(timezone.utc).timestamp() - trade["open_time"]) / 60, 1)

    # Flip outcome if costs turn a tiny win into a loss
    if pnl_usd <= 0 and pnl_usd_raw > 0:
        outcome = "LOSS"

    row = {
        "open_time": trade["timestamp"],
        "close_time": close_time,
        "entry": entry,
        "stop_loss": trade["stop_loss"],
        "take_profit": trade["take_profit"],
        "exit_price": round(exit_price, 2),
        "outcome": outcome,
        "pnl_pts": round(pnl_pts, 2),
        "pnl_usd": pnl_usd,
        "costs": total_costs,
        "rsi": trade.get("rsi", ""),
        "atr": trade.get("atr", ""),
        "duration_min": duration,
    }

    with open(_RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    log.info(f"Trade result: {outcome} | Entry: ${entry} | Exit: ${exit_price:.2f} | P&L: ${pnl_usd} (costs: -${total_costs}) | Opened: {trade['timestamp']} | Closed: {close_time}")

    # Auto-update landing page track record
    try:
        import subprocess
        gen_script = Path(__file__).parent.parent / "landing" / "generate_track_record.py"
        if gen_script.exists():
            subprocess.run(["py", str(gen_script)], capture_output=True, timeout=10)
    except Exception:
        pass

    return row


def open_trade(result):
    """Record a new trade signal as an open trade."""
    trades = _load_open_trades()
    direction = "long" if "LONG" in result["signal"] else "short"
    trade = {
        "timestamp": result["timestamp"],
        "entry": result["entry"],
        "stop_loss": result["stop_loss"],
        "take_profit": result["take_profit"],
        "direction": direction,
        "rsi": result["rsi"],
        "atr": result["atr"],
        "open_time": datetime.now(timezone.utc).timestamp(),
    }
    trades.append(trade)
    _save_open_trades(trades)
    log.info(f"Trade opened: {direction.upper()} | Entry ${trade['entry']} | SL ${trade['stop_loss']} | TP ${trade['take_profit']}")


def check_open_trades(current_price):
    """Check if any open trades hit TP or SL, or need force-close."""
    trades = _load_open_trades()
    if not trades:
        return []

    still_open = []
    closed = []
    now_ts = datetime.now(timezone.utc).timestamp()
    hour_utc = datetime.now(timezone.utc).hour

    for trade in trades:
        tp = trade["take_profit"]
        sl = trade["stop_loss"]
        direction = trade.get("direction", "long")
        open_time = trade.get("open_time", now_ts)
        duration_min = (now_ts - open_time) / 60

        # Force-close before overnight (21:00 UTC)
        if hour_utc >= CLOSE_BEFORE_HOUR_UTC:
            outcome = "WIN" if _is_in_profit(trade, current_price) else "LOSS"
            log.info(f"Force-closing trade before overnight — {outcome} at ${current_price:.2f}")
            row = _log_trade_result(trade, outcome, current_price)
            closed.append(row)
            continue

        # Force-close after max duration (4 hours)
        if duration_min >= MAX_TRADE_DURATION_MIN:
            outcome = "WIN" if _is_in_profit(trade, current_price) else "LOSS"
            log.info(f"Force-closing trade after {duration_min:.0f} min — {outcome} at ${current_price:.2f}")
            row = _log_trade_result(trade, outcome, current_price)
            closed.append(row)
            continue

        # Normal TP/SL check
        if direction == "long":
            if current_price >= tp:
                row = _log_trade_result(trade, "WIN", tp)
                closed.append(row)
            elif current_price <= sl:
                row = _log_trade_result(trade, "LOSS", sl)
                closed.append(row)
            else:
                still_open.append(trade)
        elif direction == "short":
            if current_price <= tp:
                row = _log_trade_result(trade, "WIN", tp)
                closed.append(row)
            elif current_price >= sl:
                row = _log_trade_result(trade, "LOSS", sl)
                closed.append(row)
            else:
                still_open.append(trade)

    _save_open_trades(still_open)
    return closed


def _is_in_profit(trade, current_price):
    """Check if a trade is currently in profit."""
    direction = trade.get("direction", "long")
    entry = trade["entry"]
    if direction == "long":
        return current_price > entry
    else:  # short
        return current_price < entry


def send_trade_result(row):
    """Send trade result notification to Telegram."""
    # Get running track record
    record = get_track_record()
    record_line = ""
    if record:
        record_line = (
            f"\n\n📊 Track Record: {record['total']} trades | "
            f"Win rate: {record['win_rate']}% | P&L: ${record['total_pnl']}"
        )

    # Full message for personal chat (wins + losses)
    if row["outcome"] == "WIN":
        personal_msg = (
            f"✅ *TRADE CLOSED — WIN*\n\n"
            f"Entry: `${row['entry']}`\n"
            f"Exit: `${row['exit_price']}`\n"
            f"P&L: `+{row['pnl_pts']} pts` (+${row['pnl_usd']})\n"
            f"Duration: {row['duration_min']} min"
            f"{record_line}"
        )
    else:
        personal_msg = (
            f"❌ *TRADE CLOSED — LOSS*\n\n"
            f"Entry: `${row['entry']}`\n"
            f"Exit: `${row['exit_price']}`\n"
            f"P&L: `{row['pnl_pts']} pts` (${row['pnl_usd']})\n"
            f"Duration: {row['duration_min']} min"
            f"{record_line}"
        )

    # Personal chat gets everything
    if TELEGRAM_CHAT_ID:
        _post_message(TELEGRAM_CHAT_ID, personal_msg)

    # Channel only gets wins — snappy format
    if TELEGRAM_CHANNEL_ID and row["outcome"] == "WIN":
        channel_msg = (
            f"💰 *TP HIT* +${row['pnl_usd']} 🎯\n\n"
            f"Entry `${row['entry']}` → Exit `${row['exit_price']}`\n"
            f"+{row['pnl_pts']} pts in {row['duration_min']} min\n\n"
            f"That's what patience looks like 🐐"
            f"{record_line}"
        )
        _post_message(TELEGRAM_CHANNEL_ID, channel_msg)


def get_track_record():
    """Get summary stats from trade results."""
    import csv
    if not _RESULTS_FILE.exists():
        return None

    with open(_RESULTS_FILE, encoding="utf-8") as f:
        trades = list(csv.DictReader(f))

    if not trades:
        return None

    wins = [t for t in trades if t["outcome"] == "WIN"]
    total = len(trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    total_pnl = sum(float(t["pnl_usd"]) for t in trades)

    return {
        "total": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
    }


# ==========================
# SHORT-FORM CHANNEL POSTS
# ==========================
import random

_SHORT_TIPS = [
    "Gold loves uncertainty. When markets are confused, gold moves up 📈",
    "Always set your SL before entering. No SL = gambling, not trading 🎯",
    "London session (9-16 UTC) gives the cleanest moves for gold",
    "DXY down = Gold up. Watch the dollar for clues 💡",
    "Don't chase. Wait for price to come to your zone",
    "Volume spikes at US open (13:30 UTC) — best moves happen there",
    "ATR tells you how much gold moves per hour. Use it to size your SL",
    "Gold near support + RSI oversold = highest probability buy",
    "Take partials at TP1. Let the rest ride. Protect your capital 🛡",
    "No trade is also a trade. Patience pays more than overtrading",
    "Gold respects round numbers: $4000, $4050, $4100 — use them as levels",
    "If everyone is bullish, be careful. Best entries come when others hesitate",
    "Risk 1-2% per trade max. Survival > profit",
    "News kills stops. Stay flat during FOMC and NFP unless you know what you're doing",
    "Gold and yields move inversely. Yields dropping = gold bid",
]


def send_short_channel_post(df):
    """Send a short, punchy message to the channel — like a real trader posting."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return

    # Max one channel message every 30 minutes
    _last_channel_msg_file = Path(__file__).parent.parent / "logs" / ".last_channel_msg"
    try:
        if _last_channel_msg_file.exists():
            last_ts = float(_last_channel_msg_file.read_text().strip())
            if datetime.now(timezone.utc).timestamp() - last_ts < 1800:  # 30 min
                log.info("Channel cooldown active (30 min). Skipping short post.")
                return
    except Exception:
        pass

    # Prevent repeating the same post type
    _last_type_file = Path(__file__).parent.parent / "logs" / ".last_short_type"
    last_type = ""
    if _last_type_file.exists():
        last_type = _last_type_file.read_text().strip()

    try:
        close = float(df["Close"].iloc[-1])
        high_today = float(df.tail(24)["High"].max())
        low_today = float(df.tail(24)["Low"].min())
        atr = float(df["Close"].diff().abs().rolling(14).mean().iloc[-1])
        rsi_delta = df["Close"].diff()
        rsi_gain = rsi_delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        rsi_loss = (-rsi_delta.clip(upper=0)).ewm(com=13, min_periods=14).mean()
        rsi = float((100 - (100 / (1 + rsi_gain / rsi_loss))).iloc[-1])

        # Pick a type that's different from last time
        options = ["levels", "commentary", "tip", "quick_signal", "mindset", "macro_take", "session_info"]
        if last_type in options:
            options.remove(last_type)
        # Limit session_info to once per 2 hours
        _last_session_file = Path(__file__).parent.parent / "logs" / ".last_session_post"
        try:
            if _last_session_file.exists():
                last_session_ts = float(_last_session_file.read_text().strip())
                if datetime.now(timezone.utc).timestamp() - last_session_ts < 7200:
                    if "session_info" in options:
                        options.remove("session_info")
        except Exception:
            pass
        post_type = random.choice(options)

        if post_type == "levels":
            support = round(low_today, 2)
            resistance = round(high_today, 2)
            msg = (
                f"📊 *Quick Levels*\n\n"
                f"Gold: `${close:.2f}`\n"
                f"Support: `${support}`  |  Resistance: `${resistance}`\n"
                f"Range today: `${high_today - low_today:.2f}`\n\n"
                f"Watching ${support} for a bounce 👀"
            )

        elif post_type == "commentary":
            comments = []
            if rsi > 60:
                comments.append(f"Gold at ${close:.2f} — RSI getting stretched ({rsi:.0f}). Might cool off before next push 🔄")
            elif rsi < 40:
                comments.append(f"Gold pulling back to ${close:.2f} — RSI at {rsi:.0f}, getting into buy territory 🎯")
            elif close > (high_today + low_today) / 2:
                comments.append(f"Gold holding above midrange at ${close:.2f}. Bulls still in control 📈")
            else:
                comments.append(f"Gold at ${close:.2f}, sitting in the lower half of today's range. Watching for support hold 👀")
            comments.append(f"Gold range today: ${high_today - low_today:.2f}. {'Tight range — breakout coming?' if high_today - low_today < 15 else 'Wide range — volatility is here'} ⚡")
            comments.append(f"Price action at ${close:.2f}. {'Above opening price — momentum still up' if close > low_today + (high_today - low_today) * 0.5 else 'Below midrange — watching for sellers to step in'}")
            msg = random.choice(comments)

        elif post_type == "tip":
            tip = random.choice(_SHORT_TIPS)
            msg = f"💡 *Trader Tip*\n\n{tip}"

        elif post_type == "mindset":
            mindset_msgs = [
                "🧠 The market rewards patience, not activity. Best trade today might be no trade.",
                "🧠 One good trade > ten mediocre ones. Quality over quantity always.",
                "🧠 Your edge isn't in predicting — it's in managing risk when you're wrong.",
                "🧠 The best traders I know spend 80% of their time waiting and 20% executing.",
                "🧠 Protect capital first. Profits come to those who survive.",
                "🧠 Don't revenge trade. If you got stopped out, step away for an hour.",
                "🧠 Every loss is tuition. What did the market just teach you?",
                "🧠 Trading is a marathon, not a sprint. One bad day means nothing over 100 trades.",
                "🧠 The urge to overtrade is your biggest enemy. Sit on your hands if unsure.",
                "🧠 Discipline beats talent in trading. Every single time.",
            ]
            msg = random.choice(mindset_msgs)

        elif post_type == "macro_take":
            macro_msgs = []
            if rsi > 55:
                macro_msgs.append(f"📈 Gold showing strength at ${close:.2f}. Dollar weakness supporting the bid.")
            else:
                macro_msgs.append(f"👀 Gold consolidating around ${close:.2f}. Waiting for a catalyst to move.")
            macro_msgs.append(f"🌍 Markets watching yields today. Gold reacts inversely — yields down = gold up.")
            macro_msgs.append(f"💵 DXY is key for gold right now. Weak dollar = gold runs. Strong dollar = gold pulls back.")
            macro_msgs.append(f"🏦 Central banks still buying gold in 2026. Long-term structural bid remains.")
            macro_msgs.append(f"📉 When equities sell off, gold catches a bid. Watch S&P 500 for clues.")
            msg = random.choice(macro_msgs)

        elif post_type == "session_info":
            hour_utc = datetime.now(timezone.utc).hour
            if 7 <= hour_utc < 13:
                session_msgs = [
                    "🇪🇺 London session active. Gold tends to make its first directional move now. Watch for a setup 🎯",
                    "🇪🇺 European traders at their desks. Liquidity picking up — better fills, cleaner moves",
                    "🇪🇺 London is where the big banks move gold. Follow their footprints in the order flow",
                    f"🇪🇺 London session gold at ${close:.2f}. Morning trend often sets the tone for the day",
                ]
            elif 13 <= hour_utc < 16:
                session_msgs = [
                    "🇺🇸 US session overlapping with London — highest volume of the day. Big moves happen now ⚡",
                    "🇺🇸 US open! Expect volatility spike in the next 30 min as institutional orders flow in",
                    f"🇺🇸 US/London overlap — gold at ${close:.2f}. This is prime time for entries",
                    "🇺🇸 Overlap session = maximum liquidity. Spreads tighten, moves get clean 📊",
                ]
            elif 16 <= hour_utc < 21:
                session_msgs = [
                    "🇺🇸 US session running solo. Trend continuation or reversal — stay alert for the close 📊",
                    f"🇺🇸 Afternoon NY session. Gold at ${close:.2f}. Watch for profit-taking into the close",
                    "🇺🇸 Late US session — moves tend to slow down. If no setup, call it a day",
                    "🇺🇸 Approaching close. Smart money positioning for tomorrow. Don't force trades now",
                ]
            else:
                session_msgs = [
                    "🌙 Asian session — typically quieter for gold. Range-bound action expected. Rest up for London 😴",
                    "🌙 Asia session. Gold usually consolidates here. Save your energy for London open",
                    "🌙 Low volume hours. Don't overtrade — wait for the European open for real opportunities",
                ]
            msg = random.choice(session_msgs)

        elif post_type == "quick_signal":
            support = round(low_today, 2)
            tp1 = round(close + atr, 2)
            tp2 = round(close + atr * 2, 2)
            sl = round(close - atr * 1.5, 2)

            if rsi < 55:
                msg = (
                    f"🟢 *Watching for buy*\n\n"
                    f"Zone: `${support}` - `${close:.2f}`\n"
                    f"SL: `${sl}`\n"
                    f"TP1: `${tp1}`  |  TP2: `${tp2}`\n\n"
                    f"Wait for confirmation at support before entering"
                )
            else:
                msg = (
                    f"⏳ *No clear setup right now*\n\n"
                    f"Gold at `${close:.2f}` — waiting for pullback to buy zone\n"
                    f"Buy zone: `${support}` - `${round(close - atr, 2)}`"
                )

        _post_message(TELEGRAM_CHANNEL_ID, msg)
        log.info(f"Short channel post sent ({post_type})")

        # Save last type to avoid repetition
        _last_type_file.parent.mkdir(parents=True, exist_ok=True)
        _last_type_file.write_text(post_type)

        # Save timestamp for 30-min cooldown
        _last_channel_msg_file.write_text(str(datetime.now(timezone.utc).timestamp()))

        # Track session_info timing
        if post_type == "session_info":
            _last_session_file.write_text(str(datetime.now(timezone.utc).timestamp()))

    except Exception as e:
        log.warning(f"Short post error: {e}")


# ==========================
# ENTRY POINT
# ==========================
if __name__ == "__main__":
    # Lock file to prevent duplicate instances
    _lock_file = Path(__file__).parent.parent / "logs" / ".goldpulse.lock"
    _lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Check if another instance is running
    if _lock_file.exists():
        try:
            lock_pid = int(_lock_file.read_text().strip())
            # Check if that process is still alive
            import psutil
            if psutil.pid_exists(lock_pid):
                log.info(f"Another instance (PID {lock_pid}) is running. Exiting.")
                exit(0)
        except (ImportError, ValueError):
            # psutil not available or bad pid — check by timestamp
            lock_age = time.time() - _lock_file.stat().st_mtime
            if lock_age < 600:  # less than 10 minutes old
                log.info("Another instance appears to be running (lock < 10 min old). Exiting.")
                exit(0)

    # Write our PID to lock file
    _lock_file.write_text(str(os.getpid()))

    try:
        # Send a short punchy post first (channel only)
        try:
            df = get_4h_data()
            send_short_channel_post(df)
        except Exception as e:
            log.warning(f"Short post error: {e}")

        # Wait 2-4 minutes before full signal
        time.sleep(random.randint(120, 240))

        # Full strategy cycle (detailed message)
        try:
            strategy_cycle()
        except Exception as e:
            log.error(f"Error in strategy cycle: {e}", exc_info=True)
    finally:
        # Remove lock file when done
        try:
            _lock_file.unlink()
        except Exception:
            pass
