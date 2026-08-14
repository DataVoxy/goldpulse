import yfinance as yf
import requests
import time
import logging
import os
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ==========================
# CONFIG — load from .env file if present
# ==========================
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
FMP_API_KEY  = os.environ.get("FMP_API_KEY", "")
FETCH_INTERVAL_SECONDS = 3600  # every hour

# ==========================
# LOGGING
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("signals.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==========================
# TECHNICALS (GOLD)
# ==========================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_atr(high, low, close, period=14):
    high_low   = high - low
    high_close = (high - close.shift()).abs()
    low_close  = (low  - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr

def get_gold_technicals():
    df = yf.download("GC=F", period="5d", interval="1h", progress=False, auto_adjust=True)
    # Flatten multi-level columns if present (newer yfinance versions)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty or len(df) < 20:
        raise ValueError("Insufficient gold data returned")
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["RSI"]   = compute_rsi(df["Close"])
    df["ATR"]   = compute_atr(df["High"], df["Low"], df["Close"])
    return df

# ==========================
# GLOBAL FUNDAMENTALS
# ==========================
def _download_last(ticker):
    df = yf.download(ticker, period="5d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        raise ValueError(f"No data for {ticker}")
    return float(df["Close"].iloc[-1])

def get_usd_index():
    return _download_last("DX-Y.NYB")

def get_us10y_yield():
    return _download_last("^TNX")

def get_equity_indices():
    tickers = {"SPX": "^GSPC", "DAX": "^GDAXI", "NIKKEI": "^N225"}
    result = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_download_last, v): k for k, v in tickers.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                result[key] = future.result()
            except Exception as e:
                result[key] = f"error: {e}"
    return result

def get_macro_calendar():
    """Scrape ForexFactory economic calendar — no API key needed."""
    url = "https://www.forexfactory.com/calendar"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr.calendar__row")
        events = []
        current_date = ""
        for row in rows:
            # Date cell (only present on first row of each day)
            date_cell = row.select_one("td.calendar__date span")
            if date_cell:
                current_date = date_cell.get_text(strip=True)
            currency = row.select_one("td.calendar__currency")
            title    = row.select_one("td.calendar__event span")
            impact   = row.select_one("td.calendar__impact span")
            time_cell = row.select_one("td.calendar__time")
            event_time = time_cell.get_text(strip=True) if time_cell else ""
            if title and currency:
                impact_class = " ".join(impact.get("class", [])) if impact else ""
                if "high" in impact_class or "red" in impact_class:
                    impact_level = "high"
                elif "medium" in impact_class or "ora" in impact_class:
                    impact_level = "medium"
                else:
                    impact_level = "low"
                events.append({
                    "date":    current_date,
                    "time":    event_time,
                    "country": currency.get_text(strip=True),
                    "event":   title.get_text(strip=True),
                    "impact":  impact_level,
                })
            if len(events) >= 20:
                break
        return events if events else [{"error": "No events parsed from ForexFactory"}]
    except Exception as e:
        return [{"error": str(e)}]

# ==========================
# NEWS
# ==========================
def _scrape_rss(url, label, max_items=5):
    """Generic RSS headline scraper using html.parser for compatibility."""
    try:
        from bs4 import BeautifulSoup
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=10)
        # Try xml parser first, fall back to html.parser
        try:
            soup = BeautifulSoup(resp.content, "lxml-xml")
        except Exception:
            soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")[:max_items]
        headlines = [item.find("title").get_text(strip=True) for item in items if item.find("title")]
        return headlines if headlines else [f"{label}: no items found"]
    except Exception as e:
        return [f"{label} RSS error: {e}"]

def get_region_news(region_keyword):
    """Scrape regional business news via free RSS feeds — no API needed."""
    if region_keyword == "Asia":
        # SCMP Asia business RSS
        headlines = _scrape_rss("https://www.scmp.com/rss/91/feed", "Asia")
        if "error" in (headlines[0] if headlines else ""):
            # Fallback: NHK World business
            headlines = _scrape_rss("https://www3.nhk.or.jp/rss/news/cat3.xml", "Asia")
        return headlines
    elif region_keyword == "Europe":
        return _scrape_rss("https://feeds.bbci.co.uk/news/business/rss.xml", "EU")
    elif region_keyword == "United States":
        return _scrape_rss("https://feeds.marketwatch.com/marketwatch/topstories/", "US")
    return [f"Unknown region: {region_keyword}"]

def get_gold_news():
    """Scrape gold-specific news via multiple RSS feeds."""
    # Try multiple sources in order of reliability
    sources = [
        ("https://www.mining.com/category/gold/feed/", "Mining.com"),
        ("https://www.kitco.com/rss/latest-news.rss", "Kitco"),
        ("https://feeds.reuters.com/reuters/commoditiesNews", "Reuters"),
        ("https://feeds.marketwatch.com/marketwatch/topstories/", "MarketWatch"),
    ]
    for url, label in sources:
        headlines = _scrape_rss(url, label)
        if headlines and "error" not in headlines[0].lower() and "no items" not in headlines[0].lower():
            return headlines
    return ["No gold news available"]

# ==========================
# SIGNAL LOGIC
# ==========================
def generate_signal(gold_df, usd_now, us10y_now):
    last = gold_df.iloc[-1]
    ema20      = float(last["EMA20"])
    ema50      = float(last["EMA50"])
    rsi        = float(last["RSI"])
    atr        = float(last["ATR"])
    gold_price = float(last["Close"])

    # Use rolling mean of last 20 bars as reference for DXY and yield
    usd_df  = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False, auto_adjust=True)
    tnx_df  = yf.download("^TNX",     period="5d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(usd_df.columns, pd.MultiIndex):
        usd_df.columns = usd_df.columns.get_level_values(0)
    if isinstance(tnx_df.columns, pd.MultiIndex):
        tnx_df.columns = tnx_df.columns.get_level_values(0)
    usd_series = usd_df["Close"]
    tnx_series = tnx_df["Close"]

    usd_avg  = float(usd_series.rolling(20).mean().iloc[-1]) if len(usd_series) >= 20 else usd_now
    tnx_avg  = float(tnx_series.rolling(20).mean().iloc[-1]) if len(tnx_series) >= 20 else us10y_now

    # Technical conditions
    ema_bull = ema20 > ema50
    ema_bear = ema20 < ema50
    rsi_bull = rsi > 50
    rsi_bear = rsi < 50

    # Fundamental conditions — compare current vs rolling average
    # Weaker USD and lower yields = bullish for gold
    usd_bull   = usd_now   < usd_avg   # USD weakening vs recent avg
    usd_bear   = usd_now   > usd_avg   # USD strengthening
    yield_bull = us10y_now < tnx_avg   # yields falling
    yield_bear = us10y_now > tnx_avg   # yields rising

    bullish = ema_bull and rsi_bull and usd_bull and yield_bull
    bearish = ema_bear and rsi_bear and usd_bear and yield_bear

    log.info(
        f"Signal inputs — EMA20:{ema20:.2f} EMA50:{ema50:.2f} RSI:{rsi:.1f} "
        f"ATR:{atr:.2f} Gold:{gold_price:.2f} DXY:{usd_now:.2f}/avg:{usd_avg:.2f} "
        f"10Y:{us10y_now:.2f}/avg:{tnx_avg:.2f}"
    )

    if bullish:
        return "BUY"
    elif bearish:
        return "SELL"
    else:
        return "NO TRADE"

# ==========================
# MAIN CYCLE
# ==========================
def main_cycle():
    log.info("===== Cycle start =====")

    gold      = get_gold_technicals()
    usd_index = get_usd_index()
    us10y     = get_us10y_yield()
    indices   = get_equity_indices()
    macro     = get_macro_calendar()

    asia_news = get_region_news("Asia")
    eu_news   = get_region_news("Europe")
    us_news   = get_region_news("United States")
    gold_news = get_gold_news()

    signal = generate_signal(gold, usd_index, us10y)

    last_price = float(gold["Close"].iloc[-1])
    log.info(f"Gold price : {last_price:.2f}")
    log.info(f"DXY        : {usd_index:.2f}")
    log.info(f"US 10Y     : {us10y:.2f}")
    log.info(f"Indices    : {indices}")
    log.info("Macro calendar:")
    for e in macro[:5]:
        log.info(f"  {e}")
    log.info("Asia news:")
    for h in asia_news:
        log.info(f"  {h}")
    log.info("EU news:")
    for h in eu_news:
        log.info(f"  {h}")
    log.info("US news:")
    for h in us_news:
        log.info(f"  {h}")
    log.info("Gold news:")
    for h in gold_news:
        log.info(f"  {h}")
    log.info(f">>> SIGNAL: {signal}")
    log.info("===== Cycle end =====\n")

# ==========================
# ENTRY POINT
# ==========================
if __name__ == "__main__":
    while True:
        try:
            main_cycle()
        except Exception as e:
            log.error(f"Error in cycle: {e}", exc_info=True)
        try:
            time.sleep(FETCH_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
