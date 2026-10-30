import asyncio
import os
import sys
import re
import yaml
from telethon import TelegramClient, events
from storage import DealStorage
from amazon_scraper import extract_links_from_text, resolve_amazon_link, fetch_amazon_product
from bot_notifier import TelegramBotNotifier

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

async def process_message(event_or_msg, chat_title: str, storage: DealStorage, notifier: TelegramBotNotifier, config: dict):
    msg_text = getattr(event_or_msg, "message", "") or ""
    chat_id = event_or_msg.chat_id
    msg_id = event_or_msg.id

    if storage.is_message_processed(chat_id, msg_id):
        return
    storage.mark_message_processed(chat_id, msg_id)

    links = extract_links_from_text(msg_text)
    if not links:
        return

    # Controlla se il messaggio contiene parole chiave di errore o offerta
    is_error_flagged = contains_deal_keywords(msg_text)
    always_check = config.get("filters", {}).get("check_all_amazon_links", True)

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
        if not prod.get("success") or not prod.get("is_available"):
            print(f"[{chat_title}] ASIN {asin} non disponibile o prezzo non trovato: {prod.get('error')}")
            continue

        price = prod["price"]
        discount = prod.get("discount_pct", 0.0)
        title = prod.get("title", "Prodotto Amazon")
        badge = prod.get("deal_badge", "")

        # Verifica se soddisfa i criteri di alert
        should_notify = False
        reason = ""

        if is_error_flagged:
            should_notify = True
            reason = "Segnalato come Errore/Bomba dal canale"
        elif discount >= min_discount:
            should_notify = True
            reason = f"Sconto elevato (-{int(discount)}%)"
        elif badge:
            should_notify = True
            reason = f"Badge Offerta: {badge}"

        if not should_notify:
            print(f"[{chat_title}] ASIN {asin} prezzo {price}€ (-{discount}%), scartato (sotto soglia).")
            continue

        # Verifica cooldown per non spammare
        if not storage.should_alert_asin(asin, price, cooldown_hours=config.get("filters", {}).get("cooldown_hours", 6.0)):
            print(f"[{chat_title}] ASIN {asin} già notificato di recente, saltato.")
            continue

        print(f"🚨 [ALERT CONFERMATO] {title} a {price:.2f}€ (-{discount}%)! Invio notifica...")

        # Invia notifica sul Bot Telegram con tag affiliazione e link Keepa
        snippet = msg_text.split("\n")[0] if msg_text else ""
        notifier.send_deal_alert(
            title=title,
            price=price,
            discount_pct=discount,
            deal_badge=badge or ("Errore Prezzo" if is_error_flagged else "Super Offerta"),
            asin=asin,
            channel_name=chat_title,
            original_msg_snippet=snippet
        )

        # Registra alert nel database
        storage.record_alert(asin, title, price, discount, chat_title)

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

    # Registra listener per nuovi messaggi in tempo reale
    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        chat = await event.get_chat()
        chat_title = getattr(chat, "title", getattr(chat, "username", "Canale"))
        await process_message(event.message, chat_title, storage, notifier, config)

    # Scansione iniziale degli ultimi messaggi su ogni canale (catch-up)
    print("🔍 Controllo messaggi recenti sui canali...")
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            title = getattr(entity, "title", getattr(entity, "username", str(ch)))
            async for msg in client.iter_messages(entity, limit=5):
                await process_message(msg, title, storage, notifier, config)
        except Exception as e:
            print(f"[WARN] Impossibile recuperare ultimi messaggi da {ch}: {e}")

    print("⚡ In ascolto per nuovi messaggi in tempo reale (Ctrl+C per fermare)...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArresto completato.")
