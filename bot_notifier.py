import json
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
        chat_ids: Optional[List[int]] = None,
        channel_username: str = "",
        post_url: str = "",
        product_image_bytes: Optional[bytes] = None
    ) -> bool:
        """Invia una notifica offerta. Se presente la foto del prodotto nel canale, invia un album (foto prodotto + grafico Keepa)."""
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
        
        # Formattazione nome canale (solo testo informativo, senza link al post)
        if channel_username:
            channel_display = f"{channel_name} (@{channel_username})"
        else:
            channel_display = channel_name

        # Tronca titolo per stare comodamente nel limite caption di Telegram (1024 caratteri)
        display_title = (title[:110] + "...") if len(title) > 110 else title

        msg = (
            f"🚨 <b>OFFERTA RILEVATA!</b>{badge_str}\n"
            f"📡 <b>Canale:</b> {channel_display}\n\n"
            f"📦 <b>{display_title}</b>\n\n"
            f"💰 <b>Prezzo Attuale:</b> {price:.2f} €{discount_str}\n"
        )

        if reference_price > price > 0.0:
            lbl = reference_label or "Prezzo di riferimento"
            risparmio = reference_price - price
            msg += f"📊 <b>{lbl}:</b> {reference_price:.2f} € <i>(Risparmio: -{risparmio:.2f} €)</i>\n"

        if is_new_low and previous_low:
            msg += f"🎉 <b>Nuovo Minimo Rilevato!</b> (Precedente: {previous_low:.2f} €)\n"

        if original_msg_snippet:
            clean_snippet = original_msg_snippet.replace("<", "&lt;").replace(">", "&gt;")[:120]
            msg += f"\n💬 <i>\"{clean_snippet}\"</i>\n"

        # Pulsanti inline (solo Amazon affiliato e Grafico Keepa)
        buttons = [
            {"text": "🛒 Apri su Amazon", "url": final_amazon_url}
        ]
        if keepa_url:
            buttons.append({"text": "📈 Grafico Keepa", "url": keepa_url})

        reply_markup = {"inline_keyboard": [buttons]}

        # Pre-scarica il grafico Keepa con User-Agent idoneo per evitare blocchi/403
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        keepa_bytes = None
        if photo_url:
            try:
                r = requests.get(photo_url, headers=headers, timeout=6)
                if r.status_code == 200 and r.content and r.headers.get("content-type", "").startswith("image/"):
                    keepa_bytes = r.content
            except Exception as e:
                print(f"[NOTIFIER] Impossibile scaricare grafico Keepa: {e}")

        success = True

        for chat_id in targets:
            sent = False

            # 1. Opzione Preferita: Album Telegram (MediaGroup con Foto Prodotto + Grafico Keepa)
            if product_image_bytes and (keepa_bytes or photo_url):
                try:
                    files = {
                        "prod_img": ("product.jpg", product_image_bytes, "image/jpeg")
                    }
                    media_group = [
                        {
                            "type": "photo",
                            "media": "attach://prod_img",
                            "caption": msg,
                            "parse_mode": "HTML"
                        }
                    ]

                    if keepa_bytes:
                        files["keepa_graph"] = ("keepa.png", keepa_bytes, "image/png")
                        media_group.append({
                            "type": "photo",
                            "media": "attach://keepa_graph"
                        })
                    else:
                        media_group.append({
                            "type": "photo",
                            "media": photo_url
                        })

                    res = requests.post(
                        f"{self.base_url}/sendMediaGroup",
                        data={"chat_id": chat_id, "media": json.dumps(media_group)},
                        files=files,
                        timeout=15
                    )

                    if res.status_code == 200:
                        sent = True
                        # Telegram non supporta pulsanti inline direttamente sotto un MediaGroup;
                        # inviamo un brevissimo messaggio di servizio con i bottoni interattivi
                        btn_msg = f"⚡ <b>Azioni rapide:</b> {display_title}"
                        try:
                            requests.post(
                                f"{self.base_url}/sendMessage",
                                json={
                                    "chat_id": chat_id,
                                    "text": btn_msg,
                                    "parse_mode": "HTML",
                                    "reply_markup": reply_markup
                                },
                                timeout=8
                            )
                        except Exception as be:
                            print(f"[NOTIFIER] Errore invio bottoni dopo MediaGroup: {be}")
                    else:
                        print(f"[NOTIFIER] sendMediaGroup a {chat_id} fallito ({res.status_code}: {res.text[:100]}), ripiego...")
                except Exception as e:
                    print(f"[NOTIFIER] Eccezione sendMediaGroup: {e}")

            # 2. Se MediaGroup non applicabile o fallito, prova sendPhoto singola
            if not sent:
                # Prova con la foto del prodotto dal canale
                if product_image_bytes:
                    try:
                        files = {"photo": ("product.jpg", product_image_bytes, "image/jpeg")}
                        data = {
                            "chat_id": chat_id,
                            "caption": msg,
                            "parse_mode": "HTML",
                            "reply_markup": json.dumps(reply_markup)
                        }
                        res = requests.post(f"{self.base_url}/sendPhoto", data=data, files=files, timeout=12)
                        if res.status_code == 200:
                            sent = True
                    except Exception as pe:
                        print(f"[NOTIFIER] Eccezione sendPhoto (prod_img): {pe}")

                # Oppure prova con i bytes del grafico Keepa
                elif keepa_bytes:
                    try:
                        files = {"photo": ("keepa.png", keepa_bytes, "image/png")}
                        data = {
                            "chat_id": chat_id,
                            "caption": msg,
                            "parse_mode": "HTML",
                            "reply_markup": json.dumps(reply_markup)
                        }
                        res = requests.post(f"{self.base_url}/sendPhoto", data=data, files=files, timeout=12)
                        if res.status_code == 200:
                            sent = True
                    except Exception as pe:
                        print(f"[NOTIFIER] Eccezione sendPhoto (keepa_bytes): {pe}")

                # Oppure con l'URL remoto del grafico Keepa
                elif photo_url:
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
                    except Exception as pe:
                        print(f"[NOTIFIER] Eccezione sendPhoto (photo_url): {pe}")

            # 3. Fallback finale solo testo
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
