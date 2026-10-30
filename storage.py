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
