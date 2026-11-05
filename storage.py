import sqlite3
import os
import time
from typing import Optional, Tuple

class DealStorage:
    def __init__(self, db_path: str = "data/deals.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    chat_id INTEGER,
                    message_id INTEGER,
                    processed_at REAL,
                    PRIMARY KEY (chat_id, message_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerted_deals (
                    asin TEXT PRIMARY KEY,
                    title TEXT,
                    price REAL,
                    discount_pct REAL,
                    channel_name TEXT,
                    last_alerted_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_filters (
                    chat_id INTEGER PRIMARY KEY,
                    keywords TEXT,
                    updated_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    chat_id INTEGER PRIMARY KEY,
                    min_discount_pct REAL,
                    updated_at REAL
                )
            """)
            conn.commit()

    def is_message_processed(self, chat_id: int, message_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM processed_messages WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id)
            )
            return cursor.fetchone() is not None

    def mark_message_processed(self, chat_id: int, message_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO processed_messages (chat_id, message_id, processed_at) VALUES (?, ?, ?)",
                (chat_id, message_id, time.time())
            )
            conn.commit()

    def should_alert_asin(self, asin: str, new_price: float, cooldown_hours: float = 6.0) -> bool:
        """
        Determina se notificare l'ASIN:
        - True se non è mai stato notificato
        - True se il nuovo prezzo è inferiore al prezzo precedentemente notificato
        - True se sono trascorse più di cooldown_hours
        - False altrimenti
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT price, last_alerted_at FROM alerted_deals WHERE asin = ?", (asin,))
            row = cursor.fetchone()
            if not row:
                return True
            old_price, last_time = row
            # Se il prezzo è sceso ulteriormente di almeno 1€ o 3%
            if new_price < old_price * 0.97:
                return True
            # Se è scaduto il cooldown
            if (time.time() - last_time) >= (cooldown_hours * 3600):
                return True
            return False

    def record_alert(self, asin: str, title: str, price: float, discount_pct: float, channel_name: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO alerted_deals 
                (asin, title, price, discount_pct, channel_name, last_alerted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asin, title, price, discount_pct, channel_name, time.time())
            )
            conn.commit()

    def check_and_record_low(self, asin: str, price: float) -> Tuple[bool, Optional[float]]:
        """
        Controlla se il prezzo attuale è inferiore a quello precedentemente registrato nel database.
        Restituisce (is_new_low, previous_low).
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT price FROM alerted_deals WHERE asin = ?", (asin,))
            row = cursor.fetchone()
            if not row:
                return False, None
            prev_price = row[0]
            if price < prev_price:
                return True, prev_price
            return False, prev_price

    def get_user_min_discount(self, chat_id: int, default: float) -> float:
        """Soglia di sconto personale dell'utente, o il default del config se non impostata."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT min_discount_pct FROM user_settings WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return float(default)

    def has_custom_min_discount(self, chat_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT min_discount_pct FROM user_settings WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return bool(row and row[0] is not None)

    def set_user_min_discount(self, chat_id: int, value: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO user_settings (chat_id, min_discount_pct, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET min_discount_pct = excluded.min_discount_pct, updated_at = excluded.updated_at",
                (chat_id, float(value), time.time())
            )
            conn.commit()

    def reset_user_min_discount(self, chat_id: int):
        """Torna al default del config."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM user_settings WHERE chat_id = ?", (chat_id,))
            conn.commit()

    def get_user_keywords(self, chat_id: int) -> Optional[list]:
        """
        Restituisce:
        - None se l'utente non ha impostato preferenze
        - [] (lista vuota) se l'utente vuole TUTTO (ALL)
        - [kw1, kw2, ...] se l'utente ha impostato filtri specifici
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT keywords FROM user_filters WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            raw = row[0].strip()
            if raw.upper() == "ALL" or not raw:
                return []
            return [k.strip().lower() for k in raw.split(",") if k.strip()]

    def set_user_keywords(self, chat_id: int, keywords: list):
        """Salva le parole chiave preferite per un utente. Lista vuota = 'ALL'."""
        val = ",".join([k.strip().lower() for k in keywords]) if keywords else "ALL"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_filters (chat_id, keywords, updated_at) VALUES (?, ?, ?)",
                (chat_id, val, time.time())
            )
            conn.commit()

    def matches_user_filter(self, chat_id: int, search_text: str, default_keywords: Optional[list] = None) -> bool:
        """
        Verifica se il testo dell'offerta soddisfa i filtri dell'utente:
        - Se l'utente ha scelto 'ALL' ([]): True per tutto.
        - Se l'utente ha filtri specifici: True solo se almeno una parola chiave è presente.
        - Se l'utente non ha mai configurato filtri: usa default_keywords (se vuoto -> True).
        """
        user_kw = self.get_user_keywords(chat_id)
        active_kw = user_kw if user_kw is not None else (default_keywords or [])

        # Se non c'è alcun filtro attivo, riceve tutto
        if not active_kw:
            return True

        text_lower = search_text.lower()
        return any(kw in text_lower for kw in active_kw)


MIN_DISCOUNT_MIN = 1.0
MIN_DISCOUNT_MAX = 90.0


def parse_min_discount(raw: str) -> float:
    """Accetta '25', '25%', '25,5'. Solleva ValueError se fuori range o non numerico."""
    txt = (raw or "").strip().replace("%", "").replace(",", ".")
    value = float(txt)  # ValueError se non è un numero
    if not (MIN_DISCOUNT_MIN <= value <= MIN_DISCOUNT_MAX):
        raise ValueError(f"fuori range ({MIN_DISCOUNT_MIN:g}-{MIN_DISCOUNT_MAX:g})")
    return round(value, 1)

