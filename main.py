import asyncio
import os
import sys
import re
import yaml
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from storage import DealStorage
from amazon_scraper import extract_links_from_telethon_message, resolve_amazon_link, fetch_amazon_product
from bot_notifier import TelegramBotNotifier
from bot_listener import run_bot_listener

def fetch_product_image_bytes(image_url: str):
    """Scarica la foto prodotto Amazon. Ritorna i byte o None (mai un'eccezione)."""
    if not image_url:
        print("[Foto prodotto] nessun image_url dalla pagina Amazon (il parser non l'ha trovato)")
        return None
    try:
        import requests as _rq
        r = _rq.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and r.content and ctype.startswith("image/"):
            print(f"[Foto prodotto] scaricata ({len(r.content)} byte) da {image_url}")
            return r.content
        print(f"[Foto prodotto] non valida: HTTP {r.status_code}, Content-Type '{ctype}', url {image_url}")
    except Exception as e:
        print(f"[Foto prodotto] errore download da {image_url}: {e}")
    return None


def load_config(config_path: str = "config.yaml") -> dict:
    if not os.path.exists(config_path):
        print(f"[ERRORE] File di configurazione '{config_path}' non trovato.")
        print("Copia 'config.example.yaml' in 'config.yaml' e compila i campi.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Parole chiave che identificano un possibile errore o super offerta
DEAL_KEYWORDS = [
    "errore", "bug", "minimo storico", "crollo", "bomba", "sconto",
    "prezzaccio", "regalato", "affare", "follia", "svendita"
]

def contains_deal_keywords(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in DEAL_KEYWORDS)

_alert_lock = None

def _get_lock():
    global _alert_lock
    import asyncio
    if _alert_lock is None:
        _alert_lock = asyncio.Lock()
    return _alert_lock

async def process_message(event_or_msg, chat_title: str, storage: DealStorage, notifier: TelegramBotNotifier, config: dict, chat_username: str = ""):
    msg_text = getattr(event_or_msg, "message", "") or ""
    chat_id = event_or_msg.chat_id
    msg_id = event_or_msg.id

    if not chat_username:
        chat = getattr(event_or_msg, "chat", None)
        if chat:
            chat_username = getattr(chat, "username", "") or ""

    post_url = f"https://t.me/{chat_username}/{msg_id}" if chat_username and msg_id else ""

    if storage.is_message_processed(chat_id, msg_id):
        return
    storage.mark_message_processed(chat_id, msg_id)

    links = extract_links_from_telethon_message(event_or_msg)
    if not links:
        return

    # Controlla se il messaggio contiene parole chiave di errore o offerta
    is_error_flagged = contains_deal_keywords(msg_text)
    # Default False: solo messaggi con parole chiave di errore/offerta fanno richieste ad Amazon.
    always_check = config.get("filters", {}).get("check_all_amazon_links", False)

    if not is_error_flagged and not always_check:
        return

    min_discount = config.get("filters", {}).get("min_discount_pct", 30.0)

    for link in links:
        final_url, asin = resolve_amazon_link(link)
        if not asin:
            continue

        print(f"[{chat_title}] Trovato link Amazon ASIN {asin}. Verifica prezzo in corso...")

        # Verifica su Amazon
        prod = fetch_amazon_product(asin)
        if prod.get("blocked"):
            print(f"[{chat_title}] Amazon in pausa di sicurezza, salto i link rimanenti.")
            return
        if not prod.get("success") or not prod.get("is_available"):
            print(f"[{chat_title}] ASIN {asin} non disponibile o prezzo non trovato: {prod.get('error')}")
            continue

        price = prod["price"]
        discount = prod.get("discount_pct", 0.0)
        title = prod.get("title", "Prodotto Amazon")
        badge = prod.get("deal_badge", "")

        # Destinatari: ogni utente ha la sua soglia di sconto (/soglia), default dal config.
        # Gli errori di prezzo segnalati dal canale arrivano a tutti.
        all_alert_cids = config.get("telegram_bot", {}).get("alert_chat_ids", [])
        default_kw = config.get("filters", {}).get("keywords", [])
        search_target = f"{title} {msg_text}"
        recipients = [
            cid for cid in all_alert_cids
            if storage.matches_user_filter(cid, search_target, default_kw)
            and (is_error_flagged or discount >= storage.get_user_min_discount(cid, min_discount))
        ]
        if not recipients:
            print(f"[{chat_title}] ASIN {asin} prezzo {price}€ (-{discount}%): nessun destinatario con questa soglia/filtri, saltato.")
            continue

        # Verifica cooldown per non spammare (lock per evitare duplicati tra canali)
        async with _get_lock():
            if not storage.should_alert_asin(asin, price, cooldown_hours=config.get("filters", {}).get("cooldown_hours", 6.0)):
                print(f"[{chat_title}] ASIN {asin} già notificato di recente, saltato.")
                skip = True
            else:
                storage.record_alert(asin, title, price, discount, chat_title)
                skip = False
        if skip:
            continue

        # Verifica se è un nuovo minimo rispetto allo storico registrato dal bot
        is_new_low, prev_low = storage.check_and_record_low(asin, price)
        ref_price = prod.get("reference_price", 0.0)
        ref_label = prod.get("reference_label", "")
        graph_url = prod.get("keepa_graph_url", "")

        # Immagine per l'alert: prima la foto Amazon del prodotto (i post del canale
        # spesso contengono solo il grafico Keepa), poi la foto del post come ripiego.
        prod_image_bytes = await asyncio.to_thread(fetch_product_image_bytes, prod.get("image_url", ""))

        is_image = bool(getattr(event_or_msg, "photo", None))
        if not is_image and getattr(event_or_msg, "document", None):
            mime = getattr(event_or_msg.document, "mime_type", "") or ""
            if mime.startswith("image/"):
                is_image = True
        if not prod_image_bytes and is_image:
            try:
                prod_image_bytes = await event_or_msg.download_media(file=bytes)
            except Exception as e:
                print(f"[{chat_title}] Errore download immagine dal messaggio originale: {e}")

        print(f"🚨 [ALERT CONFERMATO] {title} a {price:.2f}€ (-{discount}%)! Invio a {len(recipients)} destinatari...")

        # Invia notifica sul Bot Telegram (album con foto prodotto + grafico Keepa e pulsanti inline)
        snippet = msg_text.split("\n")[0] if msg_text else ""
        notifier.send_deal_alert(
            title=title,
            price=price,
            discount_pct=discount,
            deal_badge=badge or ("Errore Prezzo" if is_error_flagged else "Super Offerta"),
            asin=asin,
            channel_name=chat_title,
            reference_price=ref_price,
            reference_label=ref_label,
            is_new_low=is_new_low,
            previous_low=prev_low,
            keepa_graph_url=graph_url,
            original_msg_snippet="",
            chat_ids=recipients,
            channel_username=chat_username,
            post_url=post_url,
            product_image_bytes=prod_image_bytes
        )


async def background_catchup_loop(client, entities, storage, notifier, config):
    """Loop di polling di recupero ogni 90 secondi per garantire zero messaggi persi."""
    while True:
        await asyncio.sleep(90)
        for entity in entities:
            try:
                title = getattr(entity, "title", getattr(entity, "username", "Canale"))
                username = getattr(entity, "username", "") or ""
                async for msg in client.iter_messages(entity, limit=5):
                    await process_message(msg, title, storage, notifier, config, chat_username=username)
            except Exception:
                pass

async def main():
    config = load_config("config.yaml")

    tg_cfg = config.get("telegram_scraper", {})
    api_id = tg_cfg.get("api_id")
    api_hash = tg_cfg.get("api_hash")
    session_name = tg_cfg.get("session_name", "data/deal_scraper")
    os.makedirs(os.path.dirname(os.path.abspath(session_name)), exist_ok=True)
    channels = tg_cfg.get("channels", ["scontoso"])

    bot_cfg = config.get("telegram_bot", {})
    bot_token = bot_cfg.get("token")
    alert_chat_ids = bot_cfg.get("alert_chat_ids", [])

    if not api_id or not api_hash:
        print("[ERRORE] Inserisci api_id e api_hash in config.yaml (da https://my.telegram.org)")
        sys.exit(1)

    if not bot_token or not alert_chat_ids:
        print("[AVVISO] Bot token o alert_chat_ids mancanti in config.yaml. Non riceverai notifiche.")

    affiliate_tag = config.get("amazon", {}).get("affiliate_tag", "esplorandodin-21")
    storage = DealStorage(config.get("database", {}).get("path", "data/deals.db"))
    notifier = TelegramBotNotifier(token=bot_token, default_chat_ids=alert_chat_ids, affiliate_tag=affiliate_tag)

    print("═══════════════════════════════════════════════════════════")
    print("🚀 DEAL SNIPER & ERROR POLLED BOT (Telethon + Amazon)")
    print(f"📡 Canali monitorati: {channels}")
    print(f"🤖 Notifiche verso chat ID: {alert_chat_ids}")
    print("═══════════════════════════════════════════════════════════")

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()

    print("✅ Connessione Telegram Scraper attiva!")

    # Recupera i dialoghi attuali per verificare quali canali sono già iscritti
    dialogs = await client.get_dialogs()
    joined_ids = {d.id for d in dialogs}
    resolved_channels = []

    print("🔍 Verifica iscrizione e sincronizzazione canali...")
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            title = getattr(entity, "title", getattr(entity, "username", str(ch)))
            if entity.id not in joined_ids:
                try:
                    await client(JoinChannelRequest(entity))
                    print(f"  ➕ Iscrizione automatica al canale: {title} (@{ch})")
                except Exception as ej:
                    print(f"  ⚠️ Impossibile iscrivere automaticamente a {title}: {ej}")
            else:
                print(f"  ✅ Già iscritto a: {title}")
            resolved_channels.append(entity)
        except Exception as e:
            print(f"[WARN] Impossibile recuperare il canale {ch}: {e}")

    # Avvia il listener comandi del bot (/filter, /status, ecc.) in background
    if bot_token:
        asyncio.create_task(run_bot_listener(bot_token, storage, config))

    # Avvia il loop di recupero in background (polling periodico di sicurezza)
    if resolved_channels:
        asyncio.create_task(background_catchup_loop(client, resolved_channels, storage, notifier, config))

    # Registra listener per nuovi messaggi in tempo reale (Push a zero secondi)
    @client.on(events.NewMessage(chats=resolved_channels))
    async def handler(event):
        chat = await event.get_chat()
        chat_title = getattr(chat, "title", getattr(chat, "username", "Canale"))
        chat_username = getattr(chat, "username", "") or ""
        await process_message(event.message, chat_title, storage, notifier, config, chat_username=chat_username)

    # Scansione iniziale degli ultimi messaggi su ogni canale (catch-up immediato)
    print("🔍 Controllo messaggi recenti sui canali...")
    for entity in resolved_channels:
        try:
            title = getattr(entity, "title", getattr(entity, "username", "Canale"))
            username = getattr(entity, "username", "") or ""
            async for msg in client.iter_messages(entity, limit=5):
                await process_message(msg, title, storage, notifier, config, chat_username=username)
        except Exception as e:
            print(f"[WARN] Impossibile recuperare ultimi messaggi da {entity}: {e}")

    print("⚡ In ascolto per nuovi messaggi in tempo reale e polling di sicurezza attivo...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArresto completato.")
