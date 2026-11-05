import asyncio
import requests
from typing import Dict, Any, List, Optional
from storage import DealStorage, parse_min_discount
from amazon_scraper import extract_links_from_text, resolve_amazon_link, fetch_amazon_product
import io
import time
import os
from collections import deque

# Allowlist: loaded from config.yaml -> telegram_bot.allowed_chat_ids (set in run_bot_listener)
_ALLOWED_CHAT_IDS: set = set()
# Amazon lookups per user: loaded from config.yaml -> limits.*
_MAX_LOOKUPS_PER_HOUR = 20
_lookup_times: dict = {}


def _is_allowed(chat_id) -> bool:
    try:
        return int(chat_id) in _ALLOWED_CHAT_IDS
    except (TypeError, ValueError):
        return False


def _lookup_allowed(chat_id) -> bool:
    """Max N Amazon lookups per user per hour."""
    now = time.time()
    q = _lookup_times.setdefault(chat_id, deque())
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= _MAX_LOOKUPS_PER_HOUR:
        return False
    q.append(now)
    return True

def register_bot_commands(base_url: str):
    """
    Registra i comandi ufficiali nel pulsante Menu nativo di Telegram (in basso a sinistra nella chat).
    """
    commands = [
        {"command": "menu", "description": "📱 Mostra il menu principale"},
        {"command": "filter", "description": "🔍 Mostra o imposta i filtri (es. /filter ssd, nvme)"},
        {"command": "all", "description": "🔓 Ricevi tutte le offerte senza filtri"},
        {"command": "soglia", "description": "📉 Imposta la tua soglia di sconto (es. /soglia 25)"},
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

    global _ALLOWED_CHAT_IDS, _MAX_LOOKUPS_PER_HOUR
    tg_cfg = config.get("telegram_bot", {}) or {}
    _ALLOWED_CHAT_IDS = {int(x) for x in (tg_cfg.get("allowed_chat_ids") or [])}
    _MAX_LOOKUPS_PER_HOUR = int((config.get("limits", {}) or {}).get("max_amazon_lookups_per_user_per_hour", 20))
    if not _ALLOWED_CHAT_IDS:
        print("[SECURITY] telegram_bot.allowed_chat_ids vuoto: il listener ignorerà tutti i messaggi.")

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

                # Solo messaggi privati (message). I post dei canali NON sono comandi:
                # prima venivano processati e potevano far partire richieste ad Amazon.
                msg = update.get("message")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                if chat.get("type") != "private":
                    continue
                if not _is_allowed(chat_id):
                    print(f"[Security] Messaggio ignorato da chat non autorizzata: {chat_id}")
                    continue

                text = (msg.get("text") or "").strip()
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

    # Rileva link Amazon incollati dall'utente e risponde con prezzo + grafico Keepa
    links = extract_links_from_text(text)
    amazon_links = [l for l in links if 'amazon' in l.lower() or 'amzn' in l.lower()]
    if amazon_links:
        if not _lookup_allowed(chat_id):
            send_reply(base_url, chat_id, "⛔ Hai raggiunto il limite di ricerche Amazon per questa ora. Riprova più tardi.", reply_markup=DEFAULT_KEYBOARD)
            return
        for link in amazon_links[:3]:
            final_url, asin = resolve_amazon_link(link)
            if not asin:
                send_reply(base_url, chat_id, "❌ Impossibile estrarre l'ASIN dal link.", reply_markup=DEFAULT_KEYBOARD)
                continue
            prod = fetch_amazon_product(asin)
            if prod.get('blocked'):
                send_reply(base_url, chat_id, "⏸️ Amazon è in pausa di sicurezza. Riprova più tardi.", reply_markup=DEFAULT_KEYBOARD)
                return
            if not prod.get('success'):
                send_reply(base_url, chat_id, f"❌ Prodotto non disponibile: {prod.get('error')}", reply_markup=DEFAULT_KEYBOARD)
                continue
            title = prod.get('title','Prodotto Amazon')
            price = prod['price']
            discount = prod.get('discount_pct',0.0)
            ref_price = prod.get('reference_price',0.0)
            ref_label = prod.get('reference_label','')
            graph_url = prod.get('keepa_graph_url','')
            # scarica grafico keepa
            keepa_bytes = None
            try:
                r = __import__('requests').get(graph_url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
                if r.status_code==200 and r.content:
                    keepa_bytes = r.content
            except Exception:
                pass
            affiliate_tag = config.get("amazon", {}).get("affiliate_tag", "")
            tag_param = f"?tag={affiliate_tag}" if affiliate_tag else ""
            final_amazon_url = f"https://www.amazon.it/dp/{asin}{tag_param}"
            keepa_url = f"https://keepa.com/#!product/8-{asin}"
            buttons = [[{"text": "🛒 Apri su Amazon", "url": final_amazon_url}, {"text": "📈 Grafico Keepa", "url": keepa_url}]]
            reply_markup = {"inline_keyboard": buttons}
            msg = f"📦 <b>{title[:120]}</b>\n💰 Prezzo: {price:.2f} €"
            if discount>0: msg += f" (-{int(discount)}%)"
            if ref_price>price>0: msg += f"\n📊 {ref_label or 'Rif.'}: {ref_price:.2f} €"
            import requests as rq, json as _json
            if keepa_bytes:
                try:
                    resp = rq.post(f"{base_url}/sendPhoto", data={"chat_id":chat_id,"caption":msg,"parse_mode":"HTML","reply_markup":_json.dumps(reply_markup)}, files={"photo":("keepa.png",keepa_bytes,"image/png")}, timeout=12)
                    continue
                except Exception as e:
                    print(f"[BOT LISTENER] sendPhoto fail: {e}")
            send_reply(base_url, chat_id, msg, reply_markup=reply_markup)
        return

    # 1. Menu Principale, Start e Help
    if lower in ["/start", "/help", "❓ guida & comandi", "/menu", "📱 menu"]:
        reply = (
            "🎯 <b>BENVENUTO IN DEAL SNIPER BOT!</b>\n\n"
            "Monitoro i canali Telegram di offerte ed errori di prezzo Amazon in tempo reale.\n\n"
            "📋 <b>MENU COMANDI DISPONIBILI:</b>\n\n"
            "• <code>/filter ssd, nvme</code> : Imposta parole chiave (notifica solo quei prodotti)\n"
            "• <code>/all</code> : Rimuove i filtri e ricevi <b>TUTTE</b> le offerte ed errori\n"
            "• <code>/soglia 25</code> : Ricevi solo offerte con sconto ≥ 25% (<code>/soglia</code> per vedere la tua, <code>/soglia reset</code> per il default)\n"
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

    # 3b. Soglia di sconto personale (/soglia, /soglia 25, /soglia reset)
    if lower == "/soglia" or lower.startswith("/soglia "):
        arg = text[len("/soglia"):].strip()
        default_min = float(config.get("filters", {}).get("min_discount_pct", 30.0))
        if not arg:
            cur = storage.get_user_min_discount(chat_id, default_min)
            origin = "impostata da te" if storage.has_custom_min_discount(chat_id) else "default del server"
            reply = (
                f"📉 <b>La tua soglia di sconto</b>: <b>{cur:g}%</b> ({origin})\n\n"
                "Per cambiarla: <code>/soglia 25</code>\nPer tornare al default: <code>/soglia reset</code>"
            )
        elif arg.lower() == "reset":
            storage.reset_user_min_discount(chat_id)
            reply = f"✅ Soglia ripristinata al default del server: <b>{default_min:g}%</b>."
        else:
            try:
                value = parse_min_discount(arg)
            except ValueError as e:
                reply = f"⚠️ Valore non valido: {e}. Esempio: <code>/soglia 25</code>."
            else:
                storage.set_user_min_discount(chat_id, value)
                reply = f"✅ Soglia impostata a <b>{value:g}%</b>. Riceverai solo offerte con sconto pari o superiore."
        send_reply(base_url, chat_id, reply, reply_markup=DEFAULT_KEYBOARD)
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
