"""
Amazon.it scraper for deal-sniper.

Safety:
- one stable Chrome identity (curl_cffi impersonation when installed)
- global rate limit with jitter between Amazon product requests
- short per-ASIN cache, so channels posting the same deal do not multiply requests
- circuit breaker: after a CAPTCHA / 503 / 429 every Amazon request stops for a long pause
- short links are followed ONLY for official Amazon hosts (no tinyurl, bit.ly, t.co...)
- price read from the main price block of the product page
"""
import re
import random
import threading
import time
import requests
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urljoin

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
MIN_INTERVAL_S = 6.0
JITTER_S = 4.0
CACHE_TTL_S = 600
BLOCK_BASE_S = 3600
BLOCK_MAX_S = 6 * 3600
MAX_REDIRECT_HOPS = 4

FALLBACK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Only these short-link hosts are followed. Everything else is ignored.
SHORT_HOSTS = {"amzn.to", "amzn.eu", "amzlink.to", "a.co"}

# Hosts we are willing to extract links from (Amazon itself and the short links above)
LINK_REGEX = re.compile(
    r'((?:https?://)?(?:amzn\.to|amzn\.eu|amzlink\.to|a\.co|(?:www\.)?amazon\.[a-z.]+)/[^\s\)\>]+)',
    re.IGNORECASE
)

ASIN_REGEX = re.compile(r'(?:/dp/|/gp/product/|/d/|/ASIN/|/product/)([A-Z0-9]{10})', re.IGNORECASE)
AMAZON_HOST_REGEX = re.compile(r'(^|\.)amazon\.[a-z.]+$', re.IGNORECASE)

PRICE_BLOCK_IDS = (
    "corePriceDisplay_desktop_feature_div",
    "corePrice_feature_div",
    "corePrice_desktop",
    "apex_desktop",
    "buybox",
)
PRICE_BLOCK_LEN = 6000


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def extract_links_from_text(text: str) -> List[str]:
    """Trova link Amazon o shortlink ufficiali nel testo (anche senza https://)."""
    if not text:
        return []
    cleaned: List[str] = []
    for link in LINK_REGEX.findall(text):
        link = link.rstrip(".,;!*`'\"")
        if not link.startswith(("http://", "https://")):
            link = f"https://{link}"
        if link not in cleaned:
            cleaned.append(link)
    return cleaned


def extract_links_from_telethon_message(msg) -> List[str]:
    """Estrae i link da testo, entità formattate e pulsanti inline di un messaggio Telethon."""
    links: List[str] = []

    raw_text = getattr(msg, "message", "") or ""
    if raw_text:
        links.extend(extract_links_from_text(raw_text))

    for ent in (getattr(msg, "entities", None) or []):
        url = getattr(ent, "url", None)
        if url:
            links.extend(extract_links_from_text(url))

    reply_markup = getattr(msg, "reply_markup", None)
    if reply_markup and hasattr(reply_markup, "rows"):
        for row in reply_markup.rows:
            for btn in getattr(row, "buttons", []):
                btn_url = getattr(btn, "url", None)
                if btn_url:
                    links.extend(extract_links_from_text(btn_url))

    unique: List[str] = []
    for l in links:
        if l not in unique:
            unique.append(l)
    return unique


def parse_price_str(raw: str) -> Optional[float]:
    """Converte una stringa di prezzo italiana/internazionale in float."""
    if not raw:
        return None
    cleaned = raw.replace("€", "").replace("&euro;", "").replace("\xa0", " ").strip()
    m = re.search(r"([0-9.,]+)", cleaned)
    if not m:
        return None
    num_str = m.group(1).strip(".,")
    if not num_str:
        return None
    if "." in num_str and "," in num_str:
        if num_str.find(".") < num_str.find(","):
            num_str = num_str.replace(".", "").replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    elif "," in num_str:
        num_str = num_str.replace(",", ".")
    elif "." in num_str:
        parts = num_str.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and int(parts[0]) > 0:
            num_str = num_str.replace(".", "")
    try:
        val = float(num_str)
        return val if val > 0 else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
def get_default_session():
    if HAS_CFFI:
        s = cffi_requests.Session(impersonate="chrome")
        s.headers.update({"Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"})
        return s
    s = requests.Session()
    s.headers.update({
        "User-Agent": FALLBACK_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="131", "Google Chrome";v="131", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    return s


_shared_session = None
_session_lock = threading.Lock()


def get_shared_session():
    global _shared_session
    with _session_lock:
        if _shared_session is None:
            _shared_session = get_default_session()
        return _shared_session


def _reset_shared_session():
    global _shared_session
    with _session_lock:
        _shared_session = None


# ---------------------------------------------------------------------------
# Rate limit, circuit breaker, cache
# ---------------------------------------------------------------------------
_rate_lock = threading.Lock()
_next_allowed = 0.0


def _throttle():
    global _next_allowed
    with _rate_lock:
        wait = _next_allowed - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _next_allowed = time.monotonic() + MIN_INTERVAL_S + random.uniform(0, JITTER_S)


_block_lock = threading.Lock()
_blocked_until = 0.0
_block_strikes = 0


def blocked_seconds_left() -> int:
    with _block_lock:
        return max(0, int(_blocked_until - time.monotonic()))


def _trip_breaker() -> int:
    global _blocked_until, _block_strikes
    with _block_lock:
        _block_strikes += 1
        duration = min(BLOCK_BASE_S * (2 ** (_block_strikes - 1)), BLOCK_MAX_S)
        _blocked_until = time.monotonic() + duration
    _reset_shared_session()
    return int(duration)


def _record_success():
    global _block_strikes
    with _block_lock:
        _block_strikes = 0


_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(asin: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        item = _cache.get(asin)
        if item and (time.monotonic() - item[0]) < CACHE_TTL_S:
            return dict(item[1])
        if item:
            del _cache[asin]
    return None


def _cache_put(asin: str, result: Dict[str, Any]):
    with _cache_lock:
        _cache[asin] = (time.monotonic(), dict(result))
        if len(_cache) > 500:
            for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]:
                _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Link resolution (never loads the product page)
# ---------------------------------------------------------------------------
def _is_amazon_host(host: str) -> bool:
    return bool(host and AMAZON_HOST_REGEX.search(host))


def _asin_from_url(url: str) -> Optional[str]:
    m = ASIN_REGEX.search(url)
    if m:
        return m.group(1).upper()
    qs = parse_qs(urlparse(url).query)
    if qs.get("asin"):
        return qs["asin"][0].upper()
    return None


def resolve_amazon_link(url: str, session=None) -> Tuple[Optional[str], Optional[str]]:
    """Return (final_url, asin). Only Amazon hosts and official short links are followed."""
    host = (urlparse(url).hostname or "").lower()

    if _is_amazon_host(host):
        asin = _asin_from_url(url)
        if asin:
            return f"https://www.amazon.it/dp/{asin}", asin
        return url, None

    if host not in SHORT_HOSTS:
        return None, None

    s = session or get_shared_session()
    current = url
    try:
        for _ in range(MAX_REDIRECT_HOPS):
            resp = s.get(current, allow_redirects=False, timeout=8)
            location = resp.headers.get("Location")
            if resp.status_code not in (301, 302, 303, 307, 308) or not location:
                return None, None
            nxt = urljoin(current, location)
            nhost = (urlparse(nxt).hostname or "").lower()
            if _is_amazon_host(nhost):
                asin = _asin_from_url(nxt)
                if asin:
                    return f"https://www.amazon.it/dp/{asin}", asin
                return nxt, None
            if nhost not in SHORT_HOSTS:
                return None, None
            current = nxt
    except Exception:
        return None, None
    return None, None


# ---------------------------------------------------------------------------
# Product page
# ---------------------------------------------------------------------------
def _fail(asin: str, error: str, blocked: bool = False) -> Dict[str, Any]:
    return {
        "success": False,
        "asin": asin,
        "error": error,
        "price": 0.0,
        "discount_pct": 0.0,
        "deal_badge": "",
        "is_available": False,
        "blocked": blocked,
    }


def _price_region(html: str) -> str:
    for block_id in PRICE_BLOCK_IDS:
        m = re.search(r'id=["\']' + re.escape(block_id) + r'["\']', html)
        if m:
            return html[m.start(): m.start() + PRICE_BLOCK_LEN]
    return html


def _find_price(region: str) -> float:
    m = re.search(r'class=["\'][^"\']*a-offscreen[^"\']*["\']>\s*([0-9.,]+)(?:\s|&nbsp;)*(?:€|&euro;)?\s*<', region)
    if m:
        p = parse_price_str(m.group(1))
        if p:
            return p
    m_whole = re.search(r'class=["\']a-price-whole["\']>([0-9.,]+)<', region)
    m_frac = re.search(r'class=["\']a-price-fraction["\']>([0-9]+)<', region)
    if m_whole:
        w_str = m_whole.group(1).replace(".", "").replace(",", "").strip()
        f_str = m_frac.group(1).strip() if m_frac else "00"
        try:
            return float(f"{w_str}.{f_str}")
        except ValueError:
            pass
    return 0.0


def _main_image_url(html: str) -> str:
    """URL della foto principale del prodotto, dalla pagina già scaricata (nessuna richiesta extra)."""
    m = re.search(r'id=["\']landingImage["\'][^>]*data-old-hires=["\'](https://[^"\']+)["\']', html)
    if not m:
        m = re.search(r'"hiRes"\s*:\s*"(https://m\.media-amazon\.com/images/I/[^"]+)"', html)
    return m.group(1) if m else ""


def _parse_product_html(asin: str, html: str, url: str) -> Dict[str, Any]:
    title = ""
    m_title = re.search(r'id=["\']productTitle["\'][^>]*>\s*([^<]+)\s*<', html)
    if m_title:
        title = m_title.group(1).strip()

    deal_badge = ""
    m_badge = re.search(
        r'(Offerta Black Friday|Black Friday|Offerta a tempo|Offerta del giorno|Offerta lampo|Offerta di primavera)',
        html, re.IGNORECASE)
    if m_badge:
        deal_badge = m_badge.group(0).strip()

    region = _price_region(html)

    discount_pct = 0.0
    m_disc = re.search(r'class=["\'][^"\']*savingsPercentage[^"\']*["\']>([^<]+)<', region)
    if m_disc:
        try:
            discount_pct = float(m_disc.group(1).replace("-", "").replace("%", "").strip())
        except ValueError:
            pass

    price = _find_price(region)

    is_available = True
    if price == 0.0 and ("Attualmente non disponibile" in html or "Non disponibile" in html):
        is_available = False

    reference_price = 0.0
    reference_label = ""
    m_basis = re.search(
        r'(Prezzo (?:consigliato|più basso recente|mediano))[^0-9€]*([0-9.,]+)(?:\s|&nbsp;)*(?:€|&euro;)',
        region, re.I)
    if m_basis:
        reference_label = m_basis.group(1).strip()
        ref_p = parse_price_str(m_basis.group(2))
        if ref_p:
            reference_price = ref_p
    if reference_price == 0.0:
        m_span = re.search(
            r'class=["\'][^"\']*basisPrice[^"\']*["\'][^>]*>.*?class=["\']a-offscreen["\']>([0-9.,]+)(?:\s|&nbsp;)*(?:€|&euro;)',
            region, re.S | re.I)
        if m_span:
            ref_p = parse_price_str(m_span.group(1))
            if ref_p:
                reference_price = ref_p
                reference_label = "Prezzo consigliato"

    if reference_price > price > 0.0 and discount_pct == 0.0:
        discount_pct = round(((reference_price - price) / reference_price) * 100, 0)

    return {
        "success": price > 0.0,
        "asin": asin,
        "title": title or "Prodotto Amazon",
        "price": price,
        "reference_price": reference_price,
        "reference_label": reference_label,
        "discount_pct": discount_pct,
        "deal_badge": deal_badge,
        "image_url": _main_image_url(html),
        "keepa_graph_url": f"https://graph.keepa.com/pricehistory.png?asin={asin}&domain=it",
        "is_available": is_available,
        "url": url,
        "error": None if price > 0.0 else "Prezzo non trovato",
        "blocked": False,
    }


def fetch_amazon_product(asin: str, session=None, max_retries: int = 1) -> Dict[str, Any]:
    """Fetch one Amazon.it product. Check result['blocked'] before anything else."""
    cached = _cache_get(asin)
    if cached is not None:
        return cached

    left = blocked_seconds_left()
    if left > 0:
        return _fail(asin, f"Amazon in pausa ancora {left // 60 + 1} min", blocked=True)

    url = f"https://www.amazon.it/dp/{asin}"

    for attempt in range(max_retries + 1):
        s = session or get_shared_session()
        _throttle()

        left = blocked_seconds_left()
        if left > 0:
            return _fail(asin, f"Amazon in pausa ancora {left // 60 + 1} min", blocked=True)

        try:
            resp = s.get(url, timeout=12)
            html = resp.text

            is_block = (
                resp.status_code in (429, 503)
                or "api-services-support@amazon.com" in html
                or "Type the characters you see in this image" in html
                or "/errors/validateCaptcha" in html
            )
            if is_block:
                pause = _trip_breaker()
                print(f"[AMAZON] Blocco/CAPTCHA rilevato. Pausa di {pause // 60} minuti.")
                return _fail(asin, "Amazon CAPTCHA", blocked=True)

            if resp.status_code != 200:
                result = _fail(asin, f"HTTP status {resp.status_code}")
                _cache_put(asin, result)
                return result

            _record_success()
            result = _parse_product_html(asin, html, url)
            _cache_put(asin, result)
            return result

        except Exception as e:
            if attempt < max_retries:
                time.sleep(random.uniform(3, 6))
                continue
            return _fail(asin, str(e))

    return _fail(asin, "Errore sconosciuto")
