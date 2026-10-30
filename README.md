# 🎯 Deal Sniper & Error Poller

Bot autonomo e leggero in Python per il monitoraggio in tempo reale degli **errori di prezzo** e delle **super offerte Amazon** pubblicate sui canali Telegram (es. `@scontoso`, `@techleakszone`, ecc.).

Appena un canale segnala un'offerta o un errore di prezzo, il bot:
1. Cattura il messaggio in tempo reale tramite **Telethon**.
2. Risolve i redirect dei link affiliati (`amzn.to`, `amzlink.to`, ecc.) ed estrae l'**ASIN**.
3. Verifica all'istante la pagina Amazon reale:
   - Controlla disponibilità e prezzo attuale.
   - Estrae il **Prezzo di riferimento / Prezzo più basso ultimi 30 giorni** per calcolare il vero risparmio reale (evitando i falsi sconti).
   - Genera il link al **grafico storico prezzi di Keepa** (`graph.keepa.com`).
4. Invia una notifica istantanea sul tuo **bot Telegram personale**:
   - Allega la **foto del grafico storico prezzi di Keepa** degli ultimi 90 giorni.
   - Indica il prezzo attuale, il prezzo consigliato/30gg e il risparmio reale in €.
   - Segnala se il prezzo è un **Nuovo Minimo Rilevato** rispetto allo storico.
   - Fornisce i pulsanti rapidi con il tuo tag di affiliazione per l'acquisto immediato.
5. Memorizza l'evento in **SQLite** per prevenire notifiche duplicate e spam.

> **Zero costi & Zero Token**: Funziona al 100% in locale con librerie Python standard e gratuite. Nessun consumo di crediti LLM.

---

## 🏗️ Architettura & Privacy

```
[Canali Offerte Telegram]
       │
       ▼ (Event-driven Telethon - Account Scraper Dedicato)
[Deal Sniper Core]
       │  • Risoluzione Link & ASIN
       │  • Controllo Live Prezzo / Disponibilità Amazon
       │  • Deduplicazione SQLite (deals.db)
       ▼ (Se il prezzo è valido e attivo)
[BotFather Telegram Bot]
       │
       ▼
[Tuo Telegram Personale] (Notifica con bottone "Acquista")
```

**Perché usare un account dedicato per la lettura?**
- **Privacy Totale**: Il file di sessione (`data/deal_scraper.session`) appartiene a una SIM o account secondario, proteggendo le tue chat private.
- **Portabilità**: Puoi condividere il codice o la cartella con un collega senza rischiare di esporre dati personali.
- **Resilienza**: Nessun rischio di rate limit o *FloodWait* sul tuo numero principale.

---

## 🎯 Filtri Personalizzati per Utente (`/filter`)

Ogni utente può scegliere se ricevere **tutte le offerte** o **solo specifici prodotti** parlando direttamente con il bot:

- `/filter ssd, nvme` : Ricevi solo offerte o errori di prezzo per SSD e NVMe.
- `/filter lego, switch` : Ricevi solo offerte Lego o Nintendo Switch.
- `/filter all` : Rimuovi i filtri e ricevi **TUTTE** le offerte ed errori senza distinzione.
- `/filter` : Mostra i filtri attualmente attivi per la tua chat.
- `/status` : Visualizza i canali monitorati e le statistiche del bot.

*(Puoi anche scrivere direttamente `ssd, nvme` in chat senza anteporre lo slash).*

---

## ⚡ Guida Rapida all'Installazione

### 1. Prerequisiti
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose (oppure Python 3.10+)
- **API ID e API Hash Telegram**: gratuiti da [my.telegram.org](https://my.telegram.org) (nella sezione *API development tools*).
- **Token Bot Telegram**: creato in 1 minuto parlando con [@BotFather](https://t.me/BotFather).
- Il tuo **Chat ID Telegram**: invia un messaggio a [@userinfobot](https://t.me/userinfobot) per conoscere il tuo ID numerico.

---

### 2. Configurazione

Copia il file di configurazione di esempio:
```bash
cp config.example.yaml config.yaml
```

Modifica `config.yaml` inserendo le tue credenziali:
```yaml
telegram_scraper:
  api_id: 12345678                          # Da my.telegram.org
  api_hash: "abcdef0123456789abcdef0123"    # Da my.telegram.org
  channels:
    - "scontoso"
    - "techleakszone"

telegram_bot:
  token: "123456789:ABCdefGhI..."           # Token del bot da @BotFather
  alert_chat_ids:
    - 123456789                              # Il tuo ID numerico

filters:
  check_all_amazon_links: true              # Controlla tutti i link o solo "errore"
  min_discount_pct: 35.0                    # Alert se sconto >= 35%
  cooldown_hours: 6.0                       # Ore prima di poter ri-notificare lo stesso prodotto
```

---

### 3. Primo Accesso Telegram (Una tantum)

Prima di avviare il container in background, effettua il primo login interattivo per creare la sessione Telegram:

#### Metodo A: Con Python locale
```bash
pip install -r requirements.txt
python3 login.py
```
Inserisci il numero di telefono dell'account scraper e il codice OTP ricevuto su Telegram. Verrà generato il file `data/deal_scraper.session`.

#### Metodo B: Tramite Docker
```bash
docker compose run --rm deal-sniper python login.py
```

---

### 4. Avvio 24/7 con Docker Compose

Una volta generata la sessione, avvia il container in background:
```bash
docker compose up -d --build
```

Visualizza i log in tempo reale:
```bash
docker compose logs -f
```

Per fermarlo:
```bash
docker compose down
```

---

## 👥 Condivisione con un Collega

Il file `.gitignore` è già configurato per **escludere automaticamente**:
- `config.yaml`
- I file di sessione (`data/*.session`)
- Il database locale (`data/*.db`)

Il tuo collega dovrà semplicemente:
1. Clonare la cartella / repo.
2. Copiare `config.example.yaml` in `config.yaml` e inserire le sue credenziali.
3. Eseguire il login iniziale (`python3 login.py` o via docker).
4. Lanciare `docker compose up -d`.
