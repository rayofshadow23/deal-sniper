import requests
from typing import Optional, List, Dict, Any

class TelegramBotNotifier:
    def __init__(self, token: str, default_chat_ids: List[int], affiliate_tag: str = "esplorandodin-21"):
        self.token = token.strip()
        self.default_chat_ids = default_chat_ids
        self.affiliate_tag = affiliate_tag.strip() if affiliate_tag else ""
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_deal_alert(
        self,
        title: str,
        price: float,
        discount_pct: float,
        deal_badge: str,
        asin: str,
        channel_name: str,
        reference_price: float = 0.0,
        reference_label: str = "",
        is_new_low: bool = False,
        previous_low: Optional[float] = None,
        keepa_graph_url: str = "",
        amazon_url: str = "",
        original_msg_snippet: str = "",
        chat_ids: Optional[List[int]] = None
    ) -> bool:
        """Invia un messaggio di notifica offerta allegando il grafico storico prezzi di Keepa."""
        targets = chat_ids or self.default_chat_ids
        if not self.token or not targets:
            print("[NOTIFIER] Token o chat_ids mancanti, alert saltato.")
            return False

        tag_param = f"?tag={self.affiliate_tag}" if self.affiliate_tag else ""
        final_amazon_url = f"https://www.amazon.it/dp/{asin}{tag_param}" if asin else amazon_url
        keepa_url = f"https://keepa.com/#!product/8-{asin}" if asin else ""
        photo_url = keepa_graph_url or (f"https://graph.keepa.com/pricehistory.png?asin={asin}&domain=it" if asin else "")

        badge_str = f" 🔥 <b>{deal_badge}</b>" if deal_badge else ""
        discount_str = f" (📉 <b>-{int(discount_pct)}%</b>)" if discount_pct > 0 else ""
        
        # Tronca titolo per stare comodamente nel limite caption di Telegram (1024 caratteri)
        display_title = (title[:110] + "...") if len(title) > 110 else title

        msg = (
            f"🚨 <b>ERRORE DI PREZZO / SUPER OFFERTA!</b>{badge_str}\n\n"
            f"📦 <b>{display_title}</b>\n\n"
            f"💰 <b>Prezzo Attuale:</b> {price:.2f} €{discount_str}\n"
        )

        if reference_price > price > 0.0:
            lbl = reference_label or "Prezzo di riferimento"
            risparmio = reference_price - price
            msg += f"📊 <b>{lbl}:</b> {reference_price:.2f} € <i>(Risparmio: -{risparmio:.2f} €)</i>\n"

        if is_new_low and previous_low:
            msg += f"🎉 <b>Nuovo Minimo Rilevato!</b> (Precedente: {previous_low:.2f} €)\n"

        msg += f"📡 <b>Canale:</b> {channel_name}\n"

        if original_msg_snippet:
            clean_snippet = original_msg_snippet.replace("<", "&lt;").replace(">", "&gt;")[:120]
            msg += f"💬 <i>\"{clean_snippet}\"</i>\n"

        # Pulsanti inline (Amazon affiliato + Keepa)
        buttons = [
            {"text": "🛒 Apri su Amazon", "url": final_amazon_url}
        ]
        if keepa_url:
            buttons.append({"text": "📈 Grafico Keepa", "url": keepa_url})

        reply_markup = {"inline_keyboard": [buttons]}
        success = True

        for chat_id in targets:
            sent = False
            # 1. Prova a inviare come sendPhoto con il grafico Keepa
            if photo_url:
                try:
                    photo_payload = {
                        "chat_id": chat_id,
                        "photo": photo_url,
                        "caption": msg,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup
                    }
                    resp = requests.post(f"{self.base_url}/sendPhoto", json=photo_payload, timeout=12)
                    if resp.status_code == 200:
                        sent = True
                    else:
                        print(f"[NOTIFIER] sendPhoto a {chat_id} non riuscito ({resp.status_code}), ripiego su sendMessage...")
                except Exception as e:
                    print(f"[NOTIFIER] Eccezione sendPhoto: {e}")

            # 2. Se sendPhoto fallisce o non c'è foto, usa sendMessage standard
            if not sent:
                try:
                    text_payload = {
                        "chat_id": chat_id,
                        "text": msg,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                        "reply_markup": reply_markup
                    }
                    resp = requests.post(f"{self.base_url}/sendMessage", json=text_payload, timeout=8)
                    if resp.status_code != 200:
                        print(f"[NOTIFIER ERROR] Invio a {chat_id} fallito: {resp.text}")
                        success = False
                except Exception as e:
                    print(f"[NOTIFIER ERROR] Eccezione invio a {chat_id}: {e}")
                    success = False

        return success
