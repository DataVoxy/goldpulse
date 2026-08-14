"""
woning_scraper.py
-----------------
Scrapes Pararius + Huurwoningen.nl for rental listings in Limburg.
Sends new listings to Telegram, grouped by distance to Brightlands Heerlen.

Usage:
  py woning_scraper.py              # Run once, notify new listings
  py woning_scraper.py --loop       # Run every 15 min continuously
  py woning_scraper.py --reset      # Clear seen listings cache
  py woning_scraper.py --dry        # Scrape but don't send Telegram
  py woning_scraper.py --top        # Show current top 10 cheapest

Config via .env (same as GoldPulse):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Dependencies:
  py -m pip install requests beautifulsoup4 lxml cloudscraper
"""

import os
import sys
import re
import json
import time
import hashlib
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
except ImportError:
    _scraper = None

# ─── Config ──────────────────────────────────────────────────────────────────

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Search parameters
MIN_PRICE = 300
MAX_PRICE = 800

# ─── Location Data ───────────────────────────────────────────────────────────
# Grouped by distance to Brightlands Smart Services Campus, Heerlen

CITIES_DICHTBIJ = {
    # < 15 km — fietsafstand / 10 min auto
    "heerlen": 0,
    "hoensbroek": 4,
    "kerkrade": 6,
    "landgraaf": 6,
    "simpelveld": 7,
    "brunssum": 8,
    "voerendaal": 8,
    "nuth": 10,
    "eygelshoven": 5,
}

CITIES_ZUID_LIMBURG = {
    # 12-35 km — 15-30 min auto
    "valkenburg-aan-de-geul": 12,
    "beek": 13,
    "geleen": 15,
    "gulpen-wittem": 15,
    "vaals": 16,
    "meerssen": 18,
    "sittard": 20,
    "stein": 18,
    "maastricht": 25,
    "eijsden-margraten": 30,
}

CITIES_MIDDEN_LIMBURG = {
    # 30-55 km — 30-45 min auto
    "born": 22,
    "susteren": 28,
    "echt-susteren": 30,
    "roermond": 40,
    "weert": 55,
}

CITIES_NOORD_LIMBURG = {
    # > 60 km — 45+ min auto
    "peel-en-maas": 65,
    "venlo": 70,
    "horst-aan-de-maas": 75,
    "venray": 85,
}

# Combined
ALL_ZONES = {
    "🟢 <15km": CITIES_DICHTBIJ,
    "🟡 15-35km": CITIES_ZUID_LIMBURG,
    "🟠 35-60km": CITIES_MIDDEN_LIMBURG,
    "🔴 >60km": CITIES_NOORD_LIMBURG,
}
CITY_DISTANCES = {**CITIES_DICHTBIJ, **CITIES_ZUID_LIMBURG, **CITIES_MIDDEN_LIMBURG, **CITIES_NOORD_LIMBURG}
CITIES = list(CITY_DISTANCES.keys())

CHECK_INTERVAL = 900  # 15 minutes
DATA_DIR = Path(__file__).parent / "data"
SEEN_FILE = DATA_DIR / "seen_woningen.json"
LISTINGS_FILE = DATA_DIR / "all_listings.json"  # Persistent listing store
DRY_RUN = "--dry" in sys.argv

# Price filter: we want all-in under €500 ideally
# But sites show kale huur, so we search €200-€500 to catch cheap all-in listings
MIN_PRICE_ALLIN = 200
MAX_PRICE_ALLIN = 500

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "woning_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_zone(city_slug: str) -> str:
    """Get zone label for a city slug."""
    if city_slug in CITIES_DICHTBIJ:
        return "🟢 <15km"
    if city_slug in CITIES_ZUID_LIMBURG:
        return "🟡 15-35km"
    if city_slug in CITIES_MIDDEN_LIMBURG:
        return "🟠 35-60km"
    if city_slug in CITIES_NOORD_LIMBURG:
        return "🔴 >60km"
    return "⚪"


def detect_city_slug(location: str, url: str) -> str:
    """Detect which city a listing belongs to from location text or URL."""
    text = (location + " " + url).lower()
    # Check longer slugs first to avoid partial matches
    for slug in sorted(CITY_DISTANCES.keys(), key=len, reverse=True):
        name = slug.replace("-", " ").replace("aan de geul", "")
        if name.strip() in text or slug in text:
            return slug
    return ""


def parse_price(text: str) -> int:
    """Extract monthly price from text like '€ 650 per maand'."""
    if not text:
        return 0
    numbers = re.findall(r"\d+", text.replace(".", "").replace(",", ""))
    if not numbers:
        return 0
    price = int(numbers[0])
    if price > 10000:
        price = price // 100
    return price


def normalize_title(title: str) -> str:
    """Normalize for deduplication: lowercase, strip numbers/spaces."""
    t = re.sub(r"[^a-z]", "", title.lower())
    return t[:30]


# ─── Filters ─────────────────────────────────────────────────────────────────

# Keywords that indicate student-only or otherwise unsuitable listings
EXCLUDE_KEYWORDS = [
    "alleen voor studenten", "student only", "only for students",
    "uitsluitend voor studenten", "studenten woning", "studentenkamer",
    "inschrijving onderwijsinstelling", "bewijs van inschrijving",
    "enrolled at", "proof of enrollment", "student housing",
    "ad hoc", "ad hoc beheer", "anti-kraak", "antikraak", "leegstandbeheer",
    "camelot", "interveste",
]

# Specific listing URLs to exclude (known bad listings)
EXCLUDE_URLS = [
    "bfff1167/silexstraat",  # Ad Hoc Maastricht
    "cafcf29a/dr-schaepmanstraat",  # Student-only Vaals
    "2be16558/sneeuwberglaan",  # Student-only Vaals
    "25244af9/roermondsestraat",  # Student-only Venlo
]


def is_student_only(text: str) -> bool:
    """Check if listing text indicates student-only or excluded housing."""
    lower = text.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in lower:
            return True
    return False


def is_excluded_url(url: str) -> bool:
    """Check if URL matches a known bad listing."""
    for pattern in EXCLUDE_URLS:
        if pattern in url:
            return True
    return False


# ─── HTTP ────────────────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 12):
    """Fetch URL with cloudscraper. Short timeout to not waste time on blocked cities."""
    try:
        if _scraper:
            return _scraper.get(url, timeout=timeout)
        else:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "nl-NL,nl;q=0.9",
            }
            return requests.get(url, headers=headers, timeout=timeout)
    except Exception:
        return None


# ─── Persistence ─────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(list(seen)), encoding="utf-8")


def save_listings(listings: list):
    """Save all current listings for reference/analysis."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LISTINGS_FILE.write_text(
        json.dumps(listings, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_listings() -> list:
    if LISTINGS_FILE.exists():
        return json.loads(LISTINGS_FILE.read_text(encoding="utf-8"))
    return []


def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


# ─── Telegram ────────────────────────────────────────────────────────────────

def send_telegram(msg: str, preview: bool = False):
    """Send message to personal Telegram chat."""
    if DRY_RUN:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "false" if preview else "true",
        }, timeout=10)
        if resp.status_code == 429:
            # Rate limited — wait and retry
            time.sleep(3)
            requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }, timeout=10)
        elif resp.status_code != 200:
            log.warning(f"Telegram: {resp.status_code}")
    except Exception as e:
        log.error(f"Telegram: {e}")


# ─── Scrapers ────────────────────────────────────────────────────────────────

def _scrape_pararius_city(city: str) -> list:
    """Scrape one city from Pararius."""
    listings = []
    url = f"https://www.pararius.nl/huurwoningen/{city}/{MIN_PRICE}-{MAX_PRICE}"
    resp = fetch(url)
    if not resp or resp.status_code != 200:
        return listings

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("li.search-list__item--listing")

    for card in cards:
        link_el = card.select_one("a.listing-search-item__link--title")
        price_el = card.select_one(".listing-search-item__price")
        if not link_el or not price_el:
            continue

        href = link_el.get("href", "")
        full_url = f"https://www.pararius.nl{href}" if href.startswith("/") else href

        title_el = card.select_one(".listing-search-item__title")
        subtitle_el = card.select_one(".listing-search-item__sub-title")

        title = title_el.get_text(strip=True) if title_el else "Woning"
        location = subtitle_el.get_text(strip=True) if subtitle_el else city.replace("-", " ").title()
        price = parse_price(price_el.get_text(strip=True))

        if price and MIN_PRICE <= price <= MAX_PRICE:
            listings.append({
                "source": "Pararius",
                "title": title,
                "location": location,
                "price": price,
                "url": full_url,
                "city": city,
            })

    return listings


def _scrape_huurwoningen_city(city: str) -> list:
    """Scrape one city from Huurwoningen.nl."""
    listings = []
    url = f"https://www.huurwoningen.nl/in/{city}/"
    resp = fetch(url)
    if not resp or resp.status_code != 200:
        return listings

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select(".listing-search-item")
    if not cards:
        cards = soup.select("[class*='listing']")

    for card in cards:
        link_el = (
            card.select_one("a.listing-search-item__link") or
            card.select_one("a[href*='/huur/']") or
            card.find("a", href=True)
        )
        if not link_el:
            continue

        href = link_el.get("href", "")
        if not href or href == "#":
            continue
        full_url = f"https://www.huurwoningen.nl{href}" if href.startswith("/") else href

        if "/in/" in full_url and "/huur/" not in full_url and "kamer" not in full_url:
            continue

        price_el = card.select_one("[class*='price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        if not price_text:
            euro_match = card.find(string=re.compile(r"€\s*\d"))
            price_text = str(euro_match) if euro_match else ""

        price = parse_price(price_text)
        if not price or price < MIN_PRICE or price > MAX_PRICE:
            continue

        title_el = card.select_one("[class*='title']")
        title = title_el.get_text(strip=True) if title_el else "Woning"

        loc_el = card.select_one("[class*='location'], [class*='sub-title']")
        location = loc_el.get_text(strip=True) if loc_el else city.replace("-", " ").title()

        listings.append({
            "source": "Huurwoningen",
            "title": title,
            "location": location,
            "price": price,
            "url": full_url,
            "city": city,
        })

    return listings


def _scrape_kamernl() -> list:
    """
    Scrape Kamer.nl for all-in listings in Limburg.
    Kamer.nl often shows inclusive prices.
    """
    listings = []
    cities_to_try = ["heerlen", "kerkrade", "maastricht", "sittard", "brunssum"]

    for city in cities_to_try:
        url = f"https://www.kamer.nl/huren/kamers-{city}/"
        resp = fetch(url)
        if not resp or resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".room-card, .listing-card, [class*='result']")

        for card in cards:
            link_el = card.find("a", href=True)
            if not link_el:
                continue

            href = link_el.get("href", "")
            if not href or href == "#":
                continue
            full_url = f"https://www.kamer.nl{href}" if href.startswith("/") else href

            # Look for price
            price_el = card.find(string=re.compile(r"€\s*\d"))
            price_text = str(price_el) if price_el else ""
            price = parse_price(price_text)

            if not price or price < MIN_PRICE or price > MAX_PRICE:
                continue

            # Check if "inclusief" or "all-in" mentioned
            card_text = card.get_text(" ", strip=True).lower()
            is_allin = any(kw in card_text for kw in ["inclusief", "incl.", "all-in", "all in"])

            # Skip student-only
            if is_student_only(card_text):
                continue

            title_el = card.select_one("[class*='title'], h2, h3")
            title = title_el.get_text(strip=True) if title_el else "Kamer"

            # Mark all-in in title
            if is_allin:
                title = f"{title} (ALL-IN)"

            listings.append({
                "source": "Kamer.nl",
                "title": title,
                "location": city.title(),
                "price": price,
                "url": full_url,
                "city": city,
            })

        time.sleep(1)

    return listings


def _scrape_marktplaats() -> list:
    """
    Scrape Marktplaats kamers te huur in Limburg.
    Many listings are all-in from private landlords.
    """
    listings = []

    searches = [
        "https://www.marktplaats.nl/l/huizen-en-kamers/kamers-te-huur/f/limburg/11/",
    ]

    # Commercial platforms to filter out (they repost from Pararius/Huurwoningen)
    COMMERCIAL_SELLERS = [
        "huurzone", "snel wonen", "huurstunt", "huurwoningen.nl",
        "pararius", "kamernet", "rentslam", "housingnet",
        "woonruimtezoekservice", "kamergenoot", "room.nl",
    ]

    # Limburg cities that must appear in URL or title for a valid listing
    LIMBURG_CITIES_MP = [
        "heerlen", "maastricht", "kerkrade", "sittard", "geleen",
        "brunssum", "landgraaf", "roermond", "venlo", "weert",
        "valkenburg", "meerssen", "beek", "stein", "vaals",
        "simpelveld", "nuth", "hoensbroek", "eygelshoven",
        "born", "susteren", "echt", "venray", "horst", "peel",
        "eijsden", "gulpen", "margraten", "limburg",
    ]

    # Spam keywords to filter
    SPAM_KEYWORDS = [
        "velux", "dakramen", "woonadres inschrijven", "registratie",
        "zonnepanelen", "garage", "parkeerplaats", "opslag",
    ]

    for search_url in searches:
        resp = fetch(search_url, timeout=15)
        if not resp or resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")

        # Try __NEXT_DATA__ (modern Marktplaats)
        next_data = soup.select_one("script#__NEXT_DATA__")
        if next_data:
            try:
                data = json.loads(next_data.string)
                props = data.get("props", {}).get("pageProps", {})
                items = (
                    props.get("listings", []) or
                    props.get("searchResults", []) or
                    props.get("ads", []) or []
                )

                for item in items:
                    price_info = item.get("priceInfo", {}) or item.get("price", {})
                    price = 0
                    if isinstance(price_info, dict):
                        price = price_info.get("priceCents", 0) // 100
                        if not price:
                            price = parse_price(str(price_info.get("priceLabel", "")))
                    elif isinstance(price_info, (int, float)):
                        price = int(price_info)

                    if not price or price < MIN_PRICE or price > MAX_PRICE:
                        continue

                    title = item.get("title", "") or item.get("name", "Kamer")
                    location = item.get("location", {})
                    if isinstance(location, dict):
                        city_name = location.get("cityName", "Limburg")
                    else:
                        city_name = str(location) or "Limburg"

                    item_id = item.get("itemId", "") or item.get("id", "")
                    slug = item.get("url", "") or item.get("slug", "")
                    if slug:
                        full_url = f"https://www.marktplaats.nl{slug}" if slug.startswith("/") else slug
                    elif item_id:
                        full_url = f"https://www.marktplaats.nl/v/huizen-en-kamers/kamers-te-huur/{item_id}"
                    else:
                        continue

                    desc = (item.get("description", "") or "").lower()
                    combined = f"{title.lower()} {desc}"

                    # Skip commercial reposters
                    seller = item.get("sellerInformation", {}) or {}
                    seller_name = (seller.get("sellerName", "") or "").lower()
                    is_commercial = any(cs in seller_name for cs in COMMERCIAL_SELLERS)
                    if not is_commercial:
                        is_commercial = any(cs in combined for cs in COMMERCIAL_SELLERS)
                    if is_commercial:
                        continue

                    # Skip spam
                    if any(sp in combined for sp in SPAM_KEYWORDS):
                        continue

                    # Must be in Limburg (check URL and title)
                    url_and_title = f"{full_url} {title}".lower()
                    in_limburg = any(city in url_and_title for city in LIMBURG_CITIES_MP)
                    if not in_limburg:
                        continue

                    if is_student_only(combined):
                        continue

                    is_allin = any(kw in combined for kw in ["inclusief", "incl", "all-in", "all in"])
                    display_title = f"{title} (ALL-IN)" if is_allin else title
                    city_slug = detect_city_slug(city_name, full_url)

                    listings.append({
                        "source": "Marktplaats",
                        "title": display_title[:80],
                        "location": city_name,
                        "price": price,
                        "url": full_url,
                        "city": city_slug or "heerlen",
                    })

            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Fallback: HTML cards
        if not listings:
            cards = soup.select("[class*='Listing'], [data-testid*='listing']")
            for card in cards:
                link_el = card.find("a", href=True)
                if not link_el:
                    continue
                href = link_el.get("href", "")
                if "kamers-te-huur" not in href and "huizen-en-kamers" not in href:
                    continue
                full_url = f"https://www.marktplaats.nl{href}" if href.startswith("/") else href

                price_el = card.find(string=re.compile(r"€\s*[\d.]"))
                price = parse_price(str(price_el)) if price_el else 0
                if not price or price < MIN_PRICE or price > MAX_PRICE:
                    continue

                title = link_el.get_text(strip=True) or "Kamer"
                card_text = card.get_text(" ", strip=True).lower()
                if is_student_only(card_text):
                    continue
                if any(cs in card_text for cs in COMMERCIAL_SELLERS):
                    continue
                if any(sp in card_text for sp in SPAM_KEYWORDS):
                    continue
                # Must be in Limburg
                url_and_text = f"{full_url} {card_text}".lower()
                if not any(city in url_and_text for city in LIMBURG_CITIES_MP):
                    continue

                is_allin = any(kw in card_text for kw in ["inclusief", "incl", "all-in"])
                if is_allin:
                    title = f"{title} (ALL-IN)"

                listings.append({
                    "source": "Marktplaats",
                    "title": title[:80],
                    "location": "Limburg",
                    "price": price,
                    "url": full_url,
                    "city": "heerlen",
                })

        time.sleep(2)

    return listings


def _scrape_huislijn() -> list:
    """
    Scrape Huislijn.nl for rooms in Limburg.
    URL structure: huislijn.nl/kamer/nederland/limburg
    """
    listings = []
    url = "https://www.huislijn.nl/kamer/nederland/limburg"

    resp = fetch(url, timeout=15)
    if not resp or resp.status_code != 200:
        log.warning(f"Huislijn: {resp.status_code if resp else 'timeout'}")
        return listings

    soup = BeautifulSoup(resp.text, "lxml")

    # Find listing cards
    cards = soup.select(".residence-card, .card, [class*='listing'], [class*='result']")
    for card in cards:
        link_el = card.find("a", href=True)
        if not link_el:
            continue

        href = link_el.get("href", "")
        if not href or href == "#":
            continue
        full_url = f"https://www.huislijn.nl{href}" if href.startswith("/") else href

        # Price
        price_el = card.find(string=re.compile(r"€\s*\d"))
        price_text = str(price_el) if price_el else ""
        price = parse_price(price_text)

        if not price or price < MIN_PRICE or price > MAX_PRICE:
            continue

        # Title
        title_el = card.select_one("h2, h3, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else "Kamer"

        # Location
        loc_el = card.select_one("[class*='location'], [class*='city'], [class*='address']")
        location = loc_el.get_text(strip=True) if loc_el else "Limburg"

        # Skip student-only
        card_text = card.get_text(" ", strip=True).lower()
        if is_student_only(card_text):
            continue

        # Check if all-in
        is_allin = any(kw in card_text for kw in ["inclusief", "incl", "all-in"])
        if is_allin:
            title = f"{title} (ALL-IN)"

        city_slug = detect_city_slug(location, full_url)

        listings.append({
            "source": "Huislijn",
            "title": title[:80],
            "location": location,
            "price": price,
            "url": full_url,
            "city": city_slug or "heerlen",
        })

    return listings


def scrape_all_parallel() -> list:
    """Scrape all sources in parallel using threads. Much faster."""
    all_listings = []
    tasks = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        # Submit Pararius tasks
        for city in CITIES:
            tasks.append(executor.submit(_scrape_pararius_city, city))
        # Submit Huurwoningen tasks
        for city in CITIES:
            tasks.append(executor.submit(_scrape_huurwoningen_city, city))
        # Submit Kamer.nl (all-in listings)
        tasks.append(executor.submit(_scrape_kamernl))
        # Submit Marktplaats
        tasks.append(executor.submit(_scrape_marktplaats))
        # Submit Huislijn.nl
        tasks.append(executor.submit(_scrape_huislijn))

        for future in as_completed(tasks):
            try:
                result = future.result()
                all_listings.extend(result)
            except Exception as e:
                log.error(f"Task error: {e}")

    return all_listings


# ─── Deduplication ───────────────────────────────────────────────────────────

def deduplicate(listings: list) -> list:
    """
    Smart dedup:
    1. Same URL = obvious duplicate
    2. Same street + exact price (cross-platform or cross-city duplicates)
    Keep the one with the most detail.
    """
    # First pass: group by URL
    by_url = {}
    for listing in listings:
        url_id = make_id(listing["url"])
        if url_id not in by_url:
            by_url[url_id] = listing

    # Second pass: group remaining by street + price (catches cross-platform & cross-city dupes)
    unique_by_url = list(by_url.values())
    groups = {}

    for listing in unique_by_url:
        # Extract street name
        street = re.sub(r"^(Kamer|Appartement|Studio|Huis)\s+", "", listing["title"], flags=re.IGNORECASE)
        street_clean = re.sub(r"[^a-z]", "", street.lower())

        # Key: street + exact price (same woning on different platforms/pages)
        group_key = f"{street_clean}_{listing['price']}"

        if group_key not in groups:
            groups[group_key] = listing
        else:
            # Keep one with longer title (usually Pararius with house number)
            if len(listing["title"]) > len(groups[group_key]["title"]):
                groups[group_key] = listing

    return list(groups.values())


# ─── Formatting ──────────────────────────────────────────────────────────────

def format_listing_compact(listing: dict) -> str:
    """Compact Telegram format — one listing per message."""
    title = listing["title"].replace("*", "").replace("_", "").replace("`", "")
    location = listing["location"].replace("*", "").replace("_", "")
    city_slug = listing.get("city", "") or detect_city_slug(location, listing["url"])
    km = CITY_DISTANCES.get(city_slug, "?")
    zone = get_zone(city_slug) if city_slug else "⚪"

    # Estimate all-in price (kale huur + ~€130 servicekosten/energie)
    estimated_total = listing["price"] + 130

    return (
        f"🏠 *€{listing['price']}/mnd* (±€{estimated_total} all-in) — {title}\n"
        f"📍 {location}\n"
        f"📏 {zone} ({km} km)\n"
        f"⚠️ _Prijs = kale huur. Reken +€100-170 voor service/energie_\n"
        f"[Bekijk → {listing['source']}]({listing['url']})"
    )


def format_digest(new_listings: list) -> str:
    """Format a digest of new listings, sorted by price."""
    if not new_listings:
        return ""

    # Sort by price
    sorted_l = sorted(new_listings, key=lambda x: x["price"])

    lines = [f"📋 *{len(new_listings)} nieuwe woningen gevonden:*\n"]

    for i, l in enumerate(sorted_l[:15], 1):
        city_slug = l.get("city", "") or detect_city_slug(l["location"], l["url"])
        km = CITY_DISTANCES.get(city_slug, "?")
        zone_icon = get_zone(city_slug).split(" ")[0] if city_slug else "⚪"
        title_short = l["title"][:35]
        lines.append(
            f"{zone_icon} €{l['price']} — [{title_short}]({l['url']}) ({km}km)"
        )

    if len(new_listings) > 15:
        lines.append(f"\n_...en {len(new_listings) - 15} meer_")

    return "\n".join(lines)


# ─── Main Logic ──────────────────────────────────────────────────────────────

def check_detail_page(url: str) -> bool:
    """
    Check a listing's detail page for student-only or excluded keywords.
    Returns True if the listing should be EXCLUDED.
    """
    try:
        resp = fetch(url, timeout=10)
        if not resp or resp.status_code != 200:
            return False  # Can't check, don't exclude

        text = resp.text.lower()

        # Check for student-only indicators on detail page
        detail_exclude_keywords = [
            "alleen voor studenten", "student only", "only for students",
            "uitsluitend voor studenten", "inschrijving onderwijsinstelling",
            "bewijs van inschrijving", "enrolled at", "proof of enrollment",
            "studentenwoning", "studentencomplex",
            "ad hoc beheer", "anti-kraak", "antikraak", "leegstandbeheer",
            "camelot", "interveste",
        ]

        for kw in detail_exclude_keywords:
            if kw in text:
                log.info(f"    EXCLUDED (detail: '{kw}'): {url}")
                return True

        return False
    except Exception:
        return False


def run_once() -> int:
    """Scrape all sources, notify new listings. Returns count of new."""
    seen = load_seen()
    new_listings = []

    log.info("Scraping all sources in parallel...")
    t0 = time.time()
    raw = scrape_all_parallel()
    elapsed = time.time() - t0
    log.info(f"  Scraped {len(raw)} raw listings in {elapsed:.1f}s")

    # Deduplicate
    unique = deduplicate(raw)
    log.info(f"  After dedup: {len(unique)} unique listings")

    # Find new ones
    for listing in unique:
        lid = make_id(listing["url"])
        if lid not in seen:
            # Skip by title/location keywords
            combined_text = f"{listing['title']} {listing['location']}"
            if is_student_only(combined_text):
                continue
            if is_excluded_url(listing["url"]):
                continue

            # Check detail page for student-only / excluded keywords
            if check_detail_page(listing["url"]):
                seen.add(lid)  # Mark as seen so we don't check again
                continue

            seen.add(lid)
            new_listings.append(listing)

    log.info(f"  New: {len(new_listings)}")

    # Save state
    save_seen(seen)
    save_listings(unique)

    # Send to Telegram
    if new_listings and not DRY_RUN:
        # Sort new by price
        new_listings.sort(key=lambda x: x["price"])

        # If <= 10 new listings, send each individually
        if len(new_listings) <= 10:
            for listing in new_listings:
                send_telegram(format_listing_compact(listing))
                time.sleep(0.3)
        else:
            # Too many — send digest + individual top 5
            digest = format_digest(new_listings)
            send_telegram(digest)
            time.sleep(0.5)

            # Send the 5 cheapest as individual messages (clickable)
            send_telegram("⬇️ *Top 5 goedkoopst:*")
            time.sleep(0.3)
            for listing in new_listings[:5]:
                send_telegram(format_listing_compact(listing))
                time.sleep(0.3)

    log.info(f"Done. {len(new_listings)} new / {len(unique)} total / {len(seen)} tracked")
    return len(new_listings)


def show_top():
    """Show current top 10 cheapest from saved listings (filtered)."""
    listings = load_listings()
    if not listings:
        print("No listings saved yet. Run without --top first.")
        return

    # Apply same filters as run_once
    seen = load_seen()
    filtered = []
    for l in listings:
        combined_text = f"{l['title']} {l['location']}"
        if is_student_only(combined_text):
            continue
        if is_excluded_url(l["url"]):
            continue
        filtered.append(l)

    filtered.sort(key=lambda x: x["price"])
    print(f"\nTOP 10 GOEDKOOPST (van {len(filtered)} gefilterd, {len(listings)} totaal):\n")
    for i, l in enumerate(filtered[:10], 1):
        city_slug = l.get("city", "") or detect_city_slug(l["location"], l["url"])
        km = CITY_DISTANCES.get(city_slug, "?")
        print(f"{i:2}. €{l['price']:>3} | {l['title'][:40]:<40} | {km}km | {l['source']}")
        print(f"     {l['url']}")
    print()


def main():
    if "--reset" in sys.argv:
        if SEEN_FILE.exists():
            SEEN_FILE.unlink()
        if LISTINGS_FILE.exists():
            LISTINGS_FILE.unlink()
        print("✅ Cache cleared.")
        return

    if "--top" in sys.argv:
        show_top()
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)

    if "--loop" in sys.argv:
        log.info(f"Woning scraper loop started (interval: {CHECK_INTERVAL}s)")
        send_telegram(
            "🏠 *Woning scraper gestart!*\n"
            f"Elke {CHECK_INTERVAL//60} min: Pararius + Huurwoningen.nl\n"
            f"€{MIN_PRICE}-€{MAX_PRICE} | Heel Limburg\n"
            f"Steden: {len(CITIES)} | Parallel scraping"
        )
        while True:
            try:
                run_once()
            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL)
    else:
        count = run_once()
        if DRY_RUN:
            print(f"(dry run) {count} new listings found")
        else:
            print(f"✅ {count} new listings sent to Telegram")


if __name__ == "__main__":
    main()
