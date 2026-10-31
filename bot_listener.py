import asyncio
import requests
from typing import Dict, Any, List, Optional
from storage import DealStorage

def register_bot_commands(base_url: str):
    """
    Registra i comandi ufficiali nel pulsante Menu nativo di Telegram (in basso a sinistra nella chat).
    """
    commands = [
        {"command": "menu", "description": "📱 Mostra il menu principale"},
        {"command": "filter", "description": "🔍 Mostra o imposta i filtri (es. /filter ssd, nvme)"},
        {"command": "all", "description": "🔓 Ricevi tutte le offerte senza filtri"},
        {"command": "status", "description": "📊 Canali monitorati e stato bot"},
        {"command": "help", "description": "❓ Guida all'uso e spiegazione"}
    ]
    try:
        r = requests.post(f"{base_url}/setMyCommands", json={"commands": commands}, timeout=6)
        if r.status_code == 200:
            print("✅ [BOT LISTENER] Menu comandi Telegram registrato con successo (setMyCommands).")
    except Exception as e:
        print(f"[BOT LISTENER WARN] Impossibile registrare setMyCommands: {e}")

DEFAULT_KEYBOARD = {
    "keyboard": [
        [{"text": "🔍 I Miei Filtri"}, {"text": "🔓 Ricevi Tutto"}],
        [{"text": "📊 Canali & Stato"}, {"text": "❓ Guida & Comandi"}]
    ],
    "resize_keyboard": True,
    "persistent": True
}

async def run_bot_listener(bot_token: str, storage: DealStorage, config: dict):
    """
    Ascolta i comandi Telegram inviati dagli utenti al bot via getUpdates long-polling.
    Permette a ogni utente di interagire tramite menu o comandi testuali.
    """
    if not bot_token or ":" not in bot_token:
        print("[BOT LISTENER] Token bot non valido o non configurato, listener interattivo disabilitato.")
        return

    base_url = f"https://api.telegram.org/bot{bot_token}"
    register_bot_commands(base_url)

    offset = 0
    default_kw = config.get("filters", {}).get("keywords", [])

    print("🤖 [BOT LISTENER] Listener comandi utente avviato (/menu, /filter, /status, /help)...")

    while True:
        try:
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
                
                # Rileva aggiunta del bot come admin in un canale o gruppo
                if "my_chat_member" in update:
                    mcm = update["my_chat_member"]
                    c = mcm.get("chat", {})
                    print(f"📢 [CANALE/GRUPPO RILEVATO] Titolo: '{c.get('title')}' | ID: {c.get('id')}")

                # Rileva post in canali (channel_post) o messaggi normali (message)
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                chat_type = chat.get("type", "")
                chat_title = chat.get("title", "")
                text = msg.get("text", "").strip()

                if chat_type in ["channel", "supergroup", "group"]:
                    print(f"📢 [MESSAGGIO IN CANALE/GRUPPO] Titolo: '{chat_title}' | Chat ID: {chat_id} | Testo: '{text}'")

                if text:
                    handle_command(base_url, chat_id, text, storage, default_kw, config)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[BOT LISTENER WARN] Eccezione polling comandi: {e}")
            await asyncio.sleep(5)

def handle_command(base_url: str, chat_id: int, text: str, storage: DealStorage, default_kw: list, config: dict):
    """Elabora i messaggi ricevuti dal bot Telegram."""
    lower = text.lower().strip()

    # 1. Menu Principale, Start e Help
    if lower in ["/start", "/help", "❓ guida & comandi", "/menu", "📱 menu"]:
        reply = (
            "🎯 <b>BENVENUTO IN DEAL SNIPER BOT!</b>\n\n"
            "Monitoro i canali Telegram di offerte ed errori di prezzo Amazon in tempo reale.\n\n"
            "📋 <b>MENU COMANDI DISPONIBILI:</b>\n\n"
            "• <code>/filter ssd, nvme</code> : Imposta parole chiave (notifica solo quei prodotti)\n"
            "• <code>/all</code> : Rimuove i filtri e ricevi <b>TUTTE</b> le offerte ed errori\n"
            "• <code>/filter</code> : Visualizza i filtri attualmente attivi per la tua chat\n"
            "• <code>/status</code> : Canali monitorati e stato del bot\n"
            "• <code>/menu</code> : Riapre questo menu rapido\n\n"
            "<i>💡 Suggerimento: Puoi anche scrivere direttamente una o più parole chiave separate da virgola (es. <code>ssd, nvme, lego</code>)!</i>"
        )
        send_reply(base_url, chat_id, reply, reply_markup=DEFAULT_KEYBOARD)
        return

    # 2. Status & Canali Monitorati
    if lower in ["/status", "📊 canali & stato"]:
        channels = config.get("telegram_scraper", {}).get("channels", [])
        kw = storage.get_user_keywords(chat_id)
        if kw is None:
            f_str = f"Predefiniti server: {', '.join(default_kw) if default_kw else 'TUTTE'}"
        elif not kw:
            f_str = "TUTTE le offerte (nessun filtro)"
        else:
            f_str = ", ".join(kw)

        reply = (
            "📊 <b>STATO MONITORAGGIO:</b>\n\n"
            f"📡 <b>Canali monitorati ({len(channels)}):</b>\n"
            + "\n".join(f"• @{ch}" for ch in channels) + "\n\n"
            f"🎯 <b>Il tuo filtro attuale:</b> <code>{f_str}</code>\n"
            f"👤 <b>Il tuo Chat ID:</b> <code>{chat_id}</code>"
        )
        send_reply(base_url, chat_id, reply, reply_markup=DEFAULT_KEYBOARD)
        return

    # 3. Ricevi Tutto (/all o bottone)
    if lower in ["/all", "🔓 ricevi tutto", "all", "tutto", "tutti", "reset", "clear"]:
        storage.set_user_keywords(chat_id, [])
        send_reply(
            base_url,
            chat_id,
            "✅ <b>Filtri azzerati!</b> Da adesso riceverai <b>TUTTE</b> le offerte e gli errori di prezzo senza distinzione.",
            reply_markup=DEFAULT_KEYBOARD
        )
        return

    # 4. Visualizzazione Filtri Attuali
    if lower in ["/filter", "🔍 i miei filtri"]:
        kw = storage.get_user_keywords(chat_id)
        if kw is None:
            msg = f"ℹ️ Non hai ancora impostato filtri personali.\nStai usando la configurazione predefinita del server: <b>{', '.join(default_kw) if default_kw else 'TUTTO'}</b>."
        elif not kw:
            msg = "ℹ️ Sei impostato su <b>TUTTO</b>: riceverai ogni offerta ed errore di prezzo senza filtri."
        else:
            msg = f"🎯 <b>I tuoi filtri attivi attuali:</b>\n" + "\n".join(f"• <code>{k}</code>" for k in kw)
        msg += "\n\nPer modificarli: <code>/filter ssd, nvme</code>\nPer ricevere tutto: <code>/all</code>"
        send_reply(base_url, chat_id, msg, reply_markup=DEFAULT_KEYBOARD)
        return

    # 5. Impostazione Filtri con argomenti
    if lower.startswith("/filter"):
        args = text[7:].strip()
    elif not text.startswith("/"):
        args = text.strip()
    else:
        return

    raw_list = [k.strip().lower() for k in args.split(",") if k.strip()]
    if raw_list:
        storage.set_user_keywords(chat_id, raw_list)
        bullet_list = "\n".join(f"• <code>{k}</code>" for k in raw_list)
        reply = (
            "✅ <b>Filtro prodotti aggiornato con successo!</b>\n\n"
            f"Riceverai notifiche solo per offerte o errori che contengono:\n{bullet_list}\n\n"
            "<i>(Per tornare a ricevere tutto, premi '🔓 Ricevi Tutto' o digita <code>/all</code>)</i>"
        )
        send_reply(base_url, chat_id, reply, reply_markup=DEFAULT_KEYBOARD)

def send_reply(base_url: str, chat_id: int, text: str, reply_markup: Optional[dict] = None):
    """Invia un messaggio di risposta con eventuale tastiera menu."""
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(f"{base_url}/sendMessage", json=payload, timeout=8)
    except Exception as e:
        print(f"[BOT LISTENER ERROR] Impossibile inviare risposta a {chat_id}: {e}")
