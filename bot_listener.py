import asyncio
import requests
from typing import Dict, Any, List, Optional
from storage import DealStorage

async def run_bot_listener(bot_token: str, storage: DealStorage, config: dict):
    """
    Ascolta i comandi Telegram inviati dagli utenti al bot via getUpdates long-polling.
    Permette a ogni utente di impostare i propri filtri (/filter ssd, nvme o /filter all).
    """
    if not bot_token or ":" not in bot_token:
        print("[BOT LISTENER] Token bot non valido o non configurato, listener interattivo disabilitato.")
        return

    base_url = f"https://api.telegram.org/bot{bot_token}"
    offset = 0
    default_kw = config.get("filters", {}).get("keywords", [])

    print("🤖 [BOT LISTENER] Listener comandi utente avviato (/filter, /status, /help)...")

    while True:
        try:
            # Esegui la richiesta getUpdates con timeout per non bloccare l'event loop
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    f"{base_url}/getUpdates",
                    params={"offset": offset, "timeout": 20},
                    timeout=25
                )
            )

            if resp.status_code != 200:
                await asyncio.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg or "text" not in msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg["text"].strip()
                handle_command(base_url, chat_id, text, storage, default_kw, config)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[BOT LISTENER WARN] Eccezione polling comandi: {e}")
            await asyncio.sleep(5)

def handle_command(base_url: str, chat_id: int, text: str, storage: DealStorage, default_kw: list, config: dict):
    """Elabora i messaggi ricevuti dal bot Telegram."""
    lower = text.lower()

    if lower.startswith("/start") or lower.startswith("/help"):
        reply = (
            "👋 <b>Ciao! Sono Deal Sniper Bot</b>\n\n"
            "Monitoro i canali di offerte ed errori di prezzo Amazon in tempo reale.\n\n"
            "🎯 <b>Come filtrare i prodotti desiderati:</b>\n"
            "• <code>/filter ssd, nvme</code> : Notifiche solo se il prodotto contiene queste parole\n"
            "• <code>/filter all</code> : Ricevi <b>TUTTE</b> le offerte ed errori (senza filtri)\n"
            "• <code>/filter</code> : Visualizza i tuoi filtri attivi\n\n"
            "<i>Puoi anche scrivere direttamente le parole separate da virgola (es. 'ssd, nvme')!</i>\n\n"
            "📊 <b>Altri comandi:</b>\n"
            "• <code>/status</code> : Mostra i canali monitorati e stato del bot"
        )
        send_reply(base_url, chat_id, reply)
        return

    if lower.startswith("/status"):
        channels = config.get("telegram_scraper", {}).get("channels", [])
        kw = storage.get_user_keywords(chat_id)
        if kw is None:
            f_str = f"Predefiniti server: {', '.join(default_kw) if default_kw else 'TUTTE'}"
        elif not kw:
            f_str = "TUTTE le offerte (nessun filtro)"
        else:
            f_str = ", ".join(kw)

        reply = (
            "📊 <b>STATO DEAL SNIPER:</b>\n\n"
            f"📡 <b>Canali monitorati ({len(channels)}):</b>\n"
            + "\n".join(f"• @{ch}" for ch in channels) + "\n\n"
            f"🎯 <b>Il tuo filtro attivo:</b> <code>{f_str}</code>\n"
            f"👤 <b>Il tuo Chat ID:</b> <code>{chat_id}</code>"
        )
        send_reply(base_url, chat_id, reply)
        return

    # Gestione /filter o testo libero
    if lower.startswith("/filter"):
        args = text[7:].strip()
    elif not text.startswith("/"):
        # Se l'utente scrive semplicemente testo libero come "ssd" o "ssd, nvme" o "all"
        args = text.strip()
    else:
        return

    if not args:
        # Mostra i filtri attuali
        kw = storage.get_user_keywords(chat_id)
        if kw is None:
            msg = f"ℹ️ Non hai impostato filtri personalizzati.\nStai usando i filtri predefiniti del server: <b>{', '.join(default_kw) if default_kw else 'TUTTO'}</b>."
        elif not kw:
            msg = "ℹ️ Sei impostato su <b>TUTTO</b>: riceverai ogni offerta ed errore di prezzo senza filtri."
        else:
            msg = f"🎯 <b>I tuoi filtri attivi attuali:</b>\n" + "\n".join(f"• <code>{k}</code>" for k in kw)
        msg += "\n\nPer modificarli: <code>/filter ssd, nvme</code>\nPer ricevere tutto: <code>/filter all</code>"
        send_reply(base_url, chat_id, msg)
        return

    if args.lower() in ["all", "tutto", "tutti", "reset", "clear"]:
        storage.set_user_keywords(chat_id, [])
        send_reply(base_url, chat_id, "✅ <b>Filtri rimossi!</b> Ora riceverai <b>TUTTE</b> le offerte ed errori di prezzo pubblicati.")
        return

    # Imposta le parole chiave
    raw_list = [k.strip().lower() for k in args.split(",") if k.strip()]
    if raw_list:
        storage.set_user_keywords(chat_id, raw_list)
        bullet_list = "\n".join(f"• <code>{k}</code>" for k in raw_list)
        reply = (
            "✅ <b>Filtro prodotti aggiornato con successo!</b>\n\n"
            f"Riceverai notifiche solo per offerte o errori che contengono:\n{bullet_list}\n\n"
            "<i>(Per tornare a ricevere tutto in qualsiasi momento, scrivi <code>/filter all</code>)</i>"
        )
        send_reply(base_url, chat_id, reply)

def send_reply(base_url: str, chat_id: int, text: str):
    """Invia un messaggio di risposta veloce."""
    try:
        requests.post(
            f"{base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8
        )
    except Exception as e:
        print(f"[BOT LISTENER ERROR] Impossibile inviare risposta a {chat_id}: {e}")
