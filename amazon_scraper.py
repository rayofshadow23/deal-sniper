import re
import random
import time
import requests
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse, parse_qs

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"
]

LINK_REGEX = re.compile(
    r'((?:https?://)?(?:amzn.to|amzn.eu|amzlink\.to|t\.co|bit\.ly|tinyurl\.com|shorturl\.at|(?:www\.)?amazon\.[a-z.]+)/[^\s\)\>]+)',
    re.IGNORECASE
)

ASIN_REGEX = re.compile(r'(?:/dp/|/gp/product/|/d/|/ASIN/|/product/)([A-Z0-9]{10})', re.IGNORECASE)

def extract_links_from_text(text: str) -> List[str]:
    """Trova tutti i link Amazon o shortlink nel testo del messaggio (anche senza https://)."""
    if not text:
        return []
    matches = LINK_REGEX.findall(text)
    cleaned = []
    for link in matches:
        link = link.rstrip(".,;!*`'\"")
        if not link.startswith("http://") and not link.startswith("https://"):
            link = f"https://{link}"
        if link not in cleaned:
            cleaned.append(link)
    return cleaned

def extract_links_from_telethon_message(msg) -> List[str]:
    """
    Estrae tutti i link Amazon/shortlink da un messaggio Telethon:
    1. Testo o caption grezza
    2. Entità formattate (hyperlink Markdown/HTML tipo [compra](url))
    3. Pulsanti inline interattivi (reply_markup)
    """
    links: List[str] = []

    # 1. Testo del messaggio o didascalia media
    raw_text = getattr(msg, "message", "") or ""
    if raw_text:
        links.extend(extract_links_from_text(raw_text))

    # 2. Entità formattate (es. link nascosti nelle parole)
    entities = getattr(msg, "entities", None) or []
    for ent in entities:
        url = getattr(ent, "url", None)
        if url:
            links.extend(extract_links_from_text(url))

    # 3. Pulsanti interattivi sotto il post (inline buttons)
    reply_markup = getattr(msg, "reply_markup", None)
    if reply_markup and hasattr(reply_markup, "rows"):
        for row in reply_markup.rows:
            for btn in getattr(row, "buttons", []):
                btn_url = getattr(btn, "url", None)
                if btn_url:
                    links.extend(extract_links_from_text(btn_url))

    # Deduplica mantenendo l'ordine
    unique_links: List[str] = []
    for l in links:
        if l not in unique_links:
            unique_links.append(l)
    return unique_links

def parse_price_str(raw: str) -> Optional[float]:
    """Converte una stringa di prezzo italiana/internazionale in float."""
    if not raw:
        return None
    cleaned = raw.replace("€", "").replace("&euro;", "").replace("\xa0", " ").strip()
    m = re.search(r"([0-9.,]+)", cleaned)
    if not m:
        return None
    num_str = m.group(1).strip(".,")
    
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

def get_default_session() -> requests.Session:
    """Crea una sessione requests con header realistici."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
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

_shared_session: Optional[requests.Session] = None

def get_shared_session() -> requests.Session:
    global _shared_session
    if _shared_session is None:
        _shared_session = get_default_session()
    return _shared_session

def resolve_amazon_link(url: str, session: Optional[requests.Session] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Risolve redirect (es. amzn.to o amzlink.to) e restituisce (final_url, asin).
    """
    s = session or get_shared_session()
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    # Se ha già l'ASIN nel link, estrailo direttamente
    m = ASIN_REGEX.search(url)
    if m:
        asin = m.group(1).upper()
        return f"https://www.amazon.it/dp/{asin}", asin

    try:
        # Fai una richiesta con redirect abilitati
        resp = s.get(url, headers=headers, allow_redirects=True, timeout=8)
        final_url = resp.url
        m_final = ASIN_REGEX.search(final_url)
        if m_final:
            asin = m_final.group(1).upper()
            return f"https://www.amazon.it/dp/{asin}", asin
            
        # Controlla se l'ASIN è nei parametri query (es. ?asin=...)
        parsed = urlparse(final_url)
        qs = parse_qs(parsed.query)
        if "asin" in qs and qs["asin"]:
            asin = qs["asin"][0].upper()
            return f"https://www.amazon.it/dp/{asin}", asin
            
        return final_url, None
    except Exception as e:
        return None, None

def fetch_amazon_product(asin: str, session: Optional[requests.Session] = None, max_retries: int = 2) -> Dict[str, Any]:
    """
    Scraper leggero della pagina prodotto di Amazon.it per l'ASIN specificato.
    """
    url = f"https://www.amazon.it/dp/{asin}"
    s = session or get_shared_session()
    
    for attempt in range(max_retries + 1):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        try:
            resp = s.get(url, headers=headers, timeout=10)
            html = resp.text

            # Rilevamento CAPTCHA
            is_captcha = "api-services-support@amazon.com" in html or "Type the characters you see in this image" in html or resp.status_code == 503
            if is_captcha:
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
                    s = get_default_session()
                    continue
                return {
                    "success": False,
                    "asin": asin,
                    "error": "Amazon CAPTCHA",
                    "price": 0.0,
                    "is_available": False
                }

            if resp.status_code != 200:
                return {
                    "success": False,
                    "asin": asin,
                    "error": f"HTTP status {resp.status_code}",
                    "price": 0.0,
                    "is_available": False
                }

            # 1. Titolo
            title = ""
            m_title = re.search(r'id=["\']productTitle["\'][^>]*>\s*([^<]+)\s*<', html)
            if m_title:
                title = m_title.group(1).strip()

            # 2. Badge Offerta
            deal_badge = ""
            m_badge = re.search(r'(Offerta Black Friday|Black Friday|Offerta a tempo|Offerta del giorno|Offerta lampo|Offerta di primavera)', html, re.IGNORECASE)
            if m_badge:
                deal_badge = m_badge.group(0).strip()

            # 3. Sconto Percentuale
            discount_pct = 0.0
            m_disc = re.search(r'class=["\'][^"\']*savingsPercentage[^"\']*["\']>([^<]+)<', html)
            if m_disc:
                disc_str = m_disc.group(1).replace("-", "").replace("%", "").strip()
                try:
                    discount_pct = float(disc_str)
                except ValueError:
                    pass

            # 4. Prezzo BuyBox
            price = 0.0
            m_price_topay = re.search(r'class=["\'][^"\']*aok-offscreen[^"\']*["\']>\s*([0-9.,]+)\s*(?:€|&euro;)', html)
            if m_price_topay:
                p = parse_price_str(m_price_topay.group(1))
                if p:
                    price = p

            if price == 0.0:
                m_whole = re.search(r'class=["\']a-price-whole["\']>([0-9.,]+)<', html)
                m_frac = re.search(r'class=["\']a-price-fraction["\']>([0-9]+)<', html)
                if m_whole:
                    w_str = m_whole.group(1).replace(".", "").replace(",", "").strip()
                    f_str = m_frac.group(1).strip() if m_frac else "00"
                    try:
                        price = float(f"{w_str}.{f_str}")
                    except ValueError:
                        pass

            if price == 0.0:
                m_offscreen = re.search(r'class=["\']a-offscreen["\']>([0-9.,]+)\s*(?:€|&euro;)?<', html)
                if m_offscreen:
                    p = parse_price_str(m_offscreen.group(1))
                    if p:
                        price = p

            is_available = True
            if "Attualmente non disponibile" in html or "Non disponibile" in html:
                if price == 0.0:
                    is_available = False

            # 5. Prezzo di Riferimento Storico (Consigliato / Ultimi 30 giorni / Mediano)
            reference_price = 0.0
            reference_label = ""
            
            m_basis = re.search(r'(Prezzo (?:consigliato|più basso recente|mediano))[^0-9€]*([0-9.,]+)\s*(?:€|&euro;)', html, re.I)
            if m_basis:
                reference_label = m_basis.group(1).strip()
                ref_p = parse_price_str(m_basis.group(2))
                if ref_p:
                    reference_price = ref_p
            
            if reference_price == 0.0:
                m_span_basis = re.search(r'class=[\"\'][^\"\']*basisPrice[^\"\']*[\"\'][^>]*>.*?class=[\"\']a-offscreen[\"\']>([0-9.,]+)\s*(?:€|&euro;)', html, re.S | re.I)
                if m_span_basis:
                    ref_p = parse_price_str(m_span_basis.group(1))
                    if ref_p:
                        reference_price = ref_p
                        reference_label = "Prezzo consigliato"

            # Se abbiamo il prezzo di riferimento, calcoliamo lo sconto reale garantito
            if reference_price > price > 0.0:
                real_disc = round(((reference_price - price) / reference_price) * 100, 0)
                if discount_pct == 0.0:
                    discount_pct = real_disc

            keepa_graph_url = f"https://graph.keepa.com/pricehistory.png?asin={asin}&domain=it"

            return {
                "success": price > 0.0,
                "asin": asin,
                "title": title or "Prodotto Amazon",
                "price": price,
                "reference_price": reference_price,
                "reference_label": reference_label,
                "discount_pct": discount_pct,
                "deal_badge": deal_badge,
                "keepa_graph_url": keepa_graph_url,
                "is_available": is_available,
                "url": url,
                "error": None if price > 0.0 else "Prezzo non trovato"
            }

        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            return {
                "success": False,
                "asin": asin,
                "error": str(e),
                "price": 0.0,
                "is_available": False
            }
