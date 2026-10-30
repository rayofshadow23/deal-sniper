import asyncio
import os
import sys
import yaml
from telethon import TelegramClient

def main():
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print("[ERRORE] 'config.yaml' non trovato. Copia 'config.example.yaml' e compila i campi prima di avviare il login.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tg_cfg = config.get("telegram_scraper", {})
    api_id = tg_cfg.get("api_id")
    api_hash = tg_cfg.get("api_hash")
    session_name = tg_cfg.get("session_name", "data/deal_scraper")
    os.makedirs(os.path.dirname(os.path.abspath(session_name)), exist_ok=True)

    if not api_id or not api_hash:
        print("[ERRORE] api_id o api_hash mancanti in config.yaml.")
        print("Ottienili gratuitamente da https://my.telegram.org nella sezione 'API development tools'.")
        sys.exit(1)

    print("═══════════════════════════════════════════════════════════")
    print("🔑 TELEGRAM FIRST-TIME LOGIN WIZARD")
    print(f"Sessione: {session_name}.session")
    print("═══════════════════════════════════════════════════════════")
    print("Ti verrà chiesto il numero di telefono, il codice OTP ricevuto su Telegram")
    print("e l'eventuale password di sicurezza (2FA) dell'account dedicato.")
    print("Una volta completato, il file di sessione verrà salvato e non dovrai più rifarlo!\n")

    client = TelegramClient(session_name, api_id, api_hash)
    
    async def login():
        phone = input("\n📱 Inserisci il NUMERO DI TELEFONO dell'account (es. +393331234567, NON il token del bot!): ").strip()
        await client.start(phone=phone)
        me = await client.get_me()
        print("\n✅ LOGIN RIUSCITO!")
        print(f"👤 Autenticato come UTENTE: {me.first_name} (@{me.username or 'nessun username'}) [ID: {me.id}]")
        print(f"📁 File di sessione '{session_name}.session' generato con successo.")
        print("Ora puoi avviare il bot con Docker Compose!")
        await client.disconnect()

    asyncio.run(login())

if __name__ == "__main__":
    main()
