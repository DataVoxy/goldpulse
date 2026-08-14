"""
Strategy 2: SnapBack — Mean Reversion
--------------------------------------
Buys when price drops below lower Bollinger Band (oversold extreme).
Sells when price rises above upper Bollinger Band (overbought extreme).
Bets that price reverts to the mean (middle band).

Works best in ranging/choppy markets where Strategy 1 struggles.

TP = middle Bollinger Band (the mean)
SL = 1x ATR beyond the extreme

Usage:
  py core/strategy2_snapback.py
"""

import yfinance as yf
import pandas as pd
import logging
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

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

SYMBOL = "GC=F"
BB_PERIOD = 20          # Bollinger Band period
BB_STD = 2.0            # Standard deviations
ATR_PERIOD = 14
SL_ATR_MULT = 1.0       # SL = 1x ATR beyond the band
MARGIN_USD = 400
LEVERAGE = 20
EXPOSURE_USD = MARGIN_USD * LEVERAGE

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SESSIONS = {"London": (7, 16), "US": (13, 22)}
CLOSE_BEFORE_HOUR = 21

# ==========================
# LOGGING
# ==========================
_log_path = str(Path(__file__).parent.parent / "logs" / "strategy2.log")
os.makedirs(os.path.dirname(_log_path), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_path),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("snapback")

# ==========================
# DATA
# ==========================
def get_data():
    for attempt in range(3):
        df = yf.download(SYMBOL, period="60d", interval="1h", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty and len(df) >= BB_PERIOD + 10:
            return df
        time.sleep(5)
    raise ValueError("Not enough data after 3 attempts")

# ==========================
# INDICATORS
# ==========================
def compute_bollinger(close, period=BB_PERIOD, std=BB_STD):
    sma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, sma, lower

def compute_atr(high, low, close, period=ATR_PERIOD):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ==========================
# ENTRY LOGIC
# ==========================
def evaluate_entry(df):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    upper, middle, lower = compute_bollinger(close)
    atr = compute_atr(high, low, close)
    rsi = compute_rsi(close)

    last_close = float(close.iloc[-1])
    last_upper = float(upper.iloc[-1])
    last_middle = float(middle.iloc[-1])
    last_lower = float(lower.iloc[-1])
    last_atr = float(atr.iloc[-1])
    last_rsi = float(rsi.iloc[-1])

    # Band width (volatility measure)
    band_width = (last_upper - last_lower) / last_middle * 100

    # Session check
    hour_utc = datetime.now(timezone.utc).hour
    in_session = any(s <= hour_utc < e for s, e in SESSIONS.values())
    too_late = hour_utc >= CLOSE_BEFORE_HOUR

    # Entry conditions
    # BUY: price below lower band (extreme oversold) + RSI confirms
    below_lower = last_close < last_lower
    rsi_oversold = last_rsi < 30

    # SELL: price above upper band (extreme overbought) + RSI confirms
    above_upper = last_close > last_upper
    rsi_overbought = last_rsi > 70

    # Signal
    if not in_session:
        signal = "WAIT - outside session"
    elif too_late:
        signal = "TOO LATE"
    elif below_lower and rsi_oversold:
        signal = "SNAPBACK BUY"
    elif above_upper and rsi_overbought:
        signal = "SNAPBACK SELL"
    else:
        signal = "NO TRADE"

    # Calculate levels
    if "BUY" in signal:
        entry = last_close
        stop_loss = last_lower - (last_atr * SL_ATR_MULT)
        take_profit = last_middle  # Target = mean
        direction = "long"
    elif "SELL" in signal:
        entry = last_close
        stop_loss = last_upper + (last_atr * SL_ATR_MULT)
        take_profit = last_middle  # Target = mean
        direction = "short"
    else:
        entry = last_close
        stop_loss = last_close - last_atr
        take_profit = last_close + last_atr
        direction = None

    troy_oz = EXPOSURE_USD / entry
    sl_pts = abs(entry - stop_loss)
    tp_pts = abs(take_profit - entry)
    sl_dollars = round(sl_pts * troy_oz, 2)
    tp_dollars = round(tp_pts * troy_oz, 2)

    return {
        "signal": signal,
        "direction": direction,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "price": round(entry, 2),
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "sl_pts": round(sl_pts, 2),
        "tp_pts": round(tp_pts, 2),
        "sl_dollars": sl_dollars,
        "tp_dollars": tp_dollars,
        "rsi": round(last_rsi, 1),
        "atr": round(last_atr, 2),
        "bb_upper": round(last_upper, 2),
        "bb_middle": round(last_middle, 2),
        "bb_lower": round(last_lower, 2),
        "band_width": round(band_width, 2),
        "session": in_session,
    }

# ==========================
# TRADE TRACKING
# ==========================
_TRADES_FILE = Path(__file__).parent.parent / "logs" / "trades_s2.json"
_RESULTS_FILE = Path(__file__).parent.parent / "logs" / "trade_results_s2.csv"

def load_open():
    if _TRADES_FILE.exists():
        try: return json.loads(_TRADES_FILE.read_text())
        except: return []
    return []

def save_open(trades):
    os.makedirs(_TRADES_FILE.parent, exist_ok=True)
    _TRADES_FILE.write_text(json.dumps(trades, indent=2))

def open_trade(result):
    trades = load_open()
    trade = {
        "timestamp": result["timestamp"],
        "entry": result["entry"],
        "stop_loss": result["stop_loss"],
        "take_profit": result["take_profit"],
        "direction": result["direction"],
        "rsi": result["rsi"],
        "atr": result["atr"],
        "open_time": datetime.now(timezone.utc).timestamp(),
    }
    trades.append(trade)
    save_open(trades)
    log.info(f"S2 Trade opened: {result['direction'].upper()} | Entry ${trade['entry']} | SL ${trade['stop_loss']} | TP ${trade['take_profit']}")

def check_trades(current_price):
    trades = load_open()
    if not trades: return []
    still_open = []
    closed = []
    for trade in trades:
        tp = trade["take_profit"]; sl = trade["stop_loss"]
        d = trade.get("direction", "long")
        hit_tp = (current_price >= tp) if d == "long" else (current_price <= tp)
        hit_sl = (current_price <= sl) if d == "long" else (current_price >= sl)

        if hit_tp:
            closed.append(log_result(trade, "WIN", tp))
        elif hit_sl:
            closed.append(log_result(trade, "LOSS", sl))
        else:
            still_open.append(trade)
    save_open(still_open)
    return closed

def log_result(trade, outcome, exit_price):
    import csv
    os.makedirs(_RESULTS_FILE.parent, exist_ok=True)
    file_exists = _RESULTS_FILE.exists()
    entry = trade["entry"]
    if trade.get("direction") == "short":
        pnl_pts = entry - exit_price
    else:
        pnl_pts = exit_price - entry
    troy_oz = EXPOSURE_USD / entry
    pnl_usd = round(pnl_pts * troy_oz, 2)
    close_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    duration = round((datetime.now(timezone.utc).timestamp() - trade["open_time"]) / 60, 1)

    fields = ["open_time", "close_time", "direction", "entry", "stop_loss", "take_profit",
              "exit_price", "outcome", "pnl_pts", "pnl_usd", "rsi", "atr", "duration_min"]
    row = {
        "open_time": trade["timestamp"], "close_time": close_time,
        "direction": trade.get("direction", "long"),
        "entry": entry, "stop_loss": trade["stop_loss"], "take_profit": trade["take_profit"],
        "exit_price": round(exit_price, 2), "outcome": outcome,
        "pnl_pts": round(pnl_pts, 2), "pnl_usd": pnl_usd,
        "rsi": trade.get("rsi", ""), "atr": trade.get("atr", ""),
        "duration_min": duration,
    }
    with open(_RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists: writer.writeheader()
        writer.writerow(row)
    log.info(f"S2 Trade {outcome}: {trade.get('direction','long').upper()} | Entry ${entry} → Exit ${exit_price:.2f} | P&L: ${pnl_usd}")
    return row

# ==========================
# TELEGRAM
# ==========================
def send_to_personal(result):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import requests
    msg = (
        f"🔵 *SnapBack Signal*\n"
        f"`{result['timestamp']}`\n\n"
        f"*Signal:* {result['signal']}\n\n"
        f"Price: `${result['price']}`\n"
        f"BB Upper: `${result['bb_upper']}`\n"
        f"BB Middle: `${result['bb_middle']}`\n"
        f"BB Lower: `${result['bb_lower']}`\n"
        f"Band Width: `{result['band_width']}%`\n\n"
        f"RSI: `{result['rsi']}`  |  ATR: `{result['atr']}`\n\n"
        f"Entry: `${result['entry']}`\n"
        f"SL: `${result['stop_loss']}`  (-${result['sl_dollars']})\n"
        f"TP: `${result['take_profit']}`  (+${result['tp_dollars']})"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        log.info("S2 Telegram sent.")
    except Exception as e:
        log.warning(f"S2 Telegram error: {e}")

# ==========================
# MAIN CYCLE
# ==========================
def strategy2_cycle():
    log.info("===== SnapBack Check =====")
    df = get_data()
    result = evaluate_entry(df)

    log.info(f"Signal: {result['signal']} | Price: ${result['price']} | RSI: {result['rsi']}")
    log.info(f"BB: Lower ${result['bb_lower']} | Middle ${result['bb_middle']} | Upper ${result['bb_upper']}")
    log.info(f"Band Width: {result['band_width']}%")

    # Check open trades
    closed = check_trades(result["price"])
    for row in closed:
        log.info(f"S2 CLOSED: {row['outcome']} ${row['pnl_usd']}")

    # Open new trade if signal fires and no open trade
    if "BUY" in result["signal"] or "SELL" in result["signal"]:
        existing = load_open()
        if not existing:
            open_trade(result)

    # Send to personal chat (not channel — separate strategy)
    send_to_personal(result)

    log.info("===== SnapBack End =====\n")

# ==========================
# ENTRY POINT
# ==========================
if __name__ == "__main__":
    try:
        strategy2_cycle()
    except Exception as e:
        log.error(f"S2 Error: {e}", exc_info=True)
