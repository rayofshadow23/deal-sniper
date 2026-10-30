import requests
from typing import Optional, List, Dict, Any

class TelegramBotNotifier:
    def __init__(self, token: str, default_chat_ids: List[int]):
        self.token = token.strip()
        self.default_chat_ids = default_chat_ids
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_deal_alert(
        self,
        title: str,
        price: float,
        discount_pct: float,
        deal_badge: str,
        amazon_url: str,
        channel_name: str,
        original_msg_snippet: str = "",
        chat_ids: Optional[List[int]] = None
    ) -> bool:
        """Invia un messaggio di notifica offerta formattato in HTML."""
        targets = chat_ids or self.default_chat_ids
        if not self.token or not targets:
            print("[NOTIFIER] Token o chat_ids mancanti, alert saltato.")
            return False

        badge_str = f" 🔥 <b>{deal_badge}</b>" if deal_badge else ""
        discount_str = f" (📉 <b>-{int(discount_pct)}%</b>)" if discount_pct > 0 else ""
        
        # Tronca titolo se troppo lungo
        display_title = (title[:120] + "...") if len(title) > 120 else title

        msg = (
            f"🚨 <b>ERRORE DI PREZZO / SUPER OFFERTA!</b>{badge_str}\n\n"
            f"📦 <b>{display_title}</b>\n\n"
            f"💰 <b>Prezzo Attuale:</b> {price:.2f} €{discount_str}\n"
            f"📡 <b>Canale:</b> {channel_name}\n"
        )
        if original_msg_snippet:
            clean_snippet = original_msg_snippet.replace("<", "&lt;").replace(">", "&gt;")[:150]
            msg += f"💬 <i>\"{clean_snippet}\"</i>\n"

        # Inline button per aprire subito Amazon
        payload: Dict[str, Any] = {
            "parse_mode": "HTML",
            "text": msg,
            "disable_web_page_preview": False,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "🛒 Apri su Amazon", "url": amazon_url}
                    ]
                ]
            }
        }

        success = True
        for chat_id in targets:
            try:
                p = dict(payload)
                p["chat_id"] = chat_id
                resp = requests.post(f"{self.base_url}/sendMessage", json=p, timeout=8)
                if resp.status_code != 200:
                    print(f"[NOTIFIER ERROR] Invio a {chat_id} fallito: {resp.text}")
                    success = False
            except Exception as e:
                print(f"[NOTIFIER ERROR] Eccezione invio a {chat_id}: {e}")
                success = False

        return success
