"""
Test script — sends a fake ENTER LONG signal to Telegram
so you can see exactly what the real alert will look like.
"""
import requests
from pathlib import Path
import os

# Load .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")       # personal chat
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")   # public channel

if not TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)

if not CHAT_ID and not CHANNEL_ID:
    print("ERROR: Set at least TELEGRAM_CHAT_ID or TELEGRAM_CHANNEL_ID in .env")
    exit(1)

# Fake result that mimics a real ENTER LONG signal
fake_result = {
    "signal":       "ENTER LONG",
    "timestamp":    "2026-07-26 15:45 UTC",
    "margin_usd":   400,
    "leverage":     "x20",
    "exposure_usd": 8000,
    "entry":        4070.80,
    "stop_loss":    4025.91,
    "take_profit":  4085.76,
    "sl_dollars":   88.22,
    "tp_dollars":   29.41,
    "sl_tp_ratio":  "3.0:1  (SL:TP)",
    "rsi":          37.4,
    "atr":          29.93,
    "macd_hist":    0.42,
    "session":      ["London", "US"],
    "near_support": True,
    "news_block":   False,
    "too_late":     False,
}

display_signal = "✅ ENTER LONG"

msg = (
    f"🟡 *GoldPulse Alert*\n"
    f"`{fake_result['timestamp']}`\n\n"
    f"*Signal:* {display_signal}\n\n"
    f"*Position (eToro)*\n"
    f"Margin: ${fake_result['margin_usd']}  |  Leverage: {fake_result['leverage']}  |  Exposure: ${fake_result['exposure_usd']}\n\n"
    f"*Levels*\n"
    f"Entry:       `${fake_result['entry']}`\n"
    f"Stop Loss:   `${fake_result['stop_loss']}`  (-${fake_result['sl_dollars']})\n"
    f"Take Profit: `${fake_result['take_profit']}`  (+${fake_result['tp_dollars']})\n"
    f"SL:TP Ratio: `{fake_result['sl_tp_ratio']}`\n\n"
    f"*Indicators (4H)*\n"
    f"RSI: `{fake_result['rsi']}`  |  ATR: `{fake_result['atr']}`\n"
    f"MACD Hist: `{fake_result['macd_hist']}`\n\n"
    f"*Filters*\n"
    f"Session: {fake_result['session']}  |  Near support: {fake_result['near_support']}\n"
    f"News block: {fake_result['news_block']}  |  Too late: {fake_result['too_late']}"
)

def send(chat_id, label):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
    )
    if r.status_code == 200:
        print(f"✅ Message sent to {label}. Check your Telegram.")
    else:
        print(f"❌ Failed to send to {label}: {r.status_code} {r.text}")

if CHAT_ID:
    send(CHAT_ID, "personal chat")
if CHANNEL_ID:
    send(CHANNEL_ID, "channel")
