hylia è gay
# Bot Discord per traduzioni tra canali

Bot completo in **Python 3.11+ con discord.py**, con traduzioni tramite **LibreTranslate** oppure **DeepL API**. Non usa LLM: nessun consumo di token. La configurazione persiste in SQLite e i messaggi tradotti vengono inviati via webhook, con nome e avatar dell'autore.

Un messaggio di `Mario` diventa un messaggio webhook di `Mario (tradotto)`, con il suo avatar. Il suffisso si modifica in `.env`. Il badge APP/BOT e la resa grafica dipendono dal client Discord; il webhook rimane un messaggio dell'applicazione. Discord supporta nome e avatar personalizzati tramite i [parametri del webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook).

## 1. Crea il bot su Discord

1. Apri il [Discord Developer Portal](https://discord.com/developers/applications), crea un'applicazione e apri la sezione **Bot**.
2. Genera o reimposta il **token del bot** e conservalo per il file `.env`. Non usare il client secret né un token di un account personale.
3. In **Bot → Privileged Gateway Intents**, abilita **Message Content Intent** e salva. Non servono gli intent Presence o Server Members. Per applicazioni soggette alla verifica Discord, l'accesso all'intent può richiedere l'approvazione di Discord: vedi [Gateway Intents](https://docs.discord.com/developers/events/gateway#privileged-intents).
4. In **OAuth2 → URL Generator**, seleziona gli scope `bot` e `applications.commands`. Installa l'applicazione sul server tramite il link generato. Se richiesto dalle impostazioni dell'applicazione, abilita il contesto **Guild Install**.
5. Assegna al bot questi permessi nei canali da collegare: **Visualizza canale / View Channels**, **Invia messaggi / Send Messages**, **Leggi cronologia messaggi / Read Message History** e **Gestisci webhook / Manage Webhooks**. Per le eventuali anteprime dei link abilita anche **Incorpora link / Embed Links**. Verifica anche le sovrascritture di permessi della categoria e dei singoli canali.

I comandi di configurazione richiedono all'utente **Gestisci canali / Manage Channels** nel canale. Non è necessario dare **Amministratore** al bot. La creazione e la ricerca dei webhook richiedono `MANAGE_WEBHOOKS`, come indicato nella [documentazione Discord](https://docs.discord.com/developers/resources/webhook#create-webhook).

## 2. Installa le dipendenze

Estrai il progetto e apri il terminale nella cartella che contiene questo README e `requirements.txt`. Installa [Python 3.11 o successivo](https://www.python.org/downloads/).

**Windows, PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Verifica con `py --version` che Python sia almeno 3.11; va bene anche Python 3.14. Usando direttamente l'eseguibile della virtualenv non occorre modificare la policy di esecuzione di PowerShell.

**Linux / macOS:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Apri `.env` con un editor di testo e inserisci il token:

```dotenv
DISCORD_TOKEN=INCOLLA_QUI_IL_TOKEN_DEL_BOT
```

Il token va senza prefisso `Bot `. Non condividere `.env` e non inserirlo in un repository pubblico. Se il token viene esposto, reimpostalo dal Developer Portal.

Per provare subito i comandi in un server, imposta anche:

```dotenv
DISCORD_GUILD_ID=ID_NUMERICO_DEL_SERVER_DI_TEST
```

Per copiare l'ID, abilita **Modalità sviluppatore** nelle impostazioni avanzate di Discord e usa **Copia ID server** dal menu del server. Se lasci il valore vuoto, il bot registra i comandi globalmente; la loro comparsa nei client può richiedere un aggiornamento di Discord.

Mantieni lo stesso ambito di registrazione durante i test e per un bot dedicato a un solo server. Passare da un ID server alla registrazione globale, o cambiare ID, non cancella i comandi registrati in precedenza nell'altro ambito: possono rimanere vecchie voci o duplicati nel client. Usa l'ambito globale quando devi distribuire i comandi su più server.

## 3. Scegli il motore di traduzione

### Opzione A: LibreTranslate sul tuo computer

È la configurazione predefinita: il software è gratuito e il testo viene elaborato dalla tua istanza. Restano il consumo di risorse del computer e gli eventuali costi dell'hosting. Per Windows usa Docker Desktop con container Linux; su Linux è sufficiente Docker Engine con Compose.

Dalla cartella del progetto esegui:

```bash
docker compose up -d
docker compose logs -f libretranslate
```

Il primo avvio scarica i modelli di italiano, francese e inglese e può richiedere diversi minuti. Attendi che il servizio sia pronto; `Ctrl+C` chiude la visualizzazione dei log ma lascia il container acceso. Controlla lo stato con `docker compose ps` e apri [l'elenco delle lingue locali](http://127.0.0.1:5000/languages): devono essere presenti `it`, `fr` ed `en`.

In `.env` lascia:

```dotenv
TRANSLATION_PROVIDER=libretranslate
LIBRETRANSLATE_URL=http://127.0.0.1:5000
LIBRETRANSLATE_API_KEY=
```

`compose.yaml` avvia solo LibreTranslate; il bot si avvia separatamente al punto 4. L'API è esposta solo su `127.0.0.1`, e un volume Docker conserva i modelli. Il file usa l'[immagine ufficiale v1.9.6](https://hub.docker.com/r/libretranslate/libretranslate/tags), la release stabile verificata il 17 settembre 2026. Percorso del volume e configurazione derivano dal [Compose ufficiale](https://github.com/LibreTranslate/LibreTranslate/blob/v1.9.6/docker-compose.yml) e dalle [opzioni di installazione](https://docs.libretranslate.com/guides/installation/#arguments).

Puoi usare un'istanza LibreTranslate esterna impostando il suo URL base e la relativa API key. Un'istanza pubblica non è necessariamente gratuita o senza limiti: verifica le condizioni del gestore. Per connessioni esterne usa un endpoint HTTPS. Se il bot gira su un altro computer, `127.0.0.1` indica quel computer, quindi dovrai configurare un servizio raggiungibile da lì.

### Opzione B: DeepL API Free

Crea un account per **DeepL API** e genera una chiave API. Un normale account per il sito o l'app di traduzione DeepL non sostituisce il piano API. In `.env` imposta:

```dotenv
TRANSLATION_PROVIDER=deepl
DEEPL_API_KEY=INCOLLA_QUI_LA_CHIAVE_API
DEEPL_API_URL=https://api-free.deepl.com
```

Con questa opzione non serve Docker né un'istanza LibreTranslate. Alla verifica del 17 settembre 2026, DeepL documenta **500.000 caratteri al mese** per API Free: vedi [limiti e conteggio dei consumi](https://developers.deepl.com/docs/resources/usage-limits). Il limite è in caratteri, non in token; il testo tradotto verso due lingue distinte comporta due traduzioni. Il bot riutilizza la traduzione per tutti i canali che hanno la stessa lingua. Per API Pro l'URL è `https://api.deepl.com`; endpoint e chiavi sono descritti nella [documentazione di autenticazione](https://developers.deepl.com/docs/getting-started/auth).

## 4. Avvia il bot

Puoi controllare prima la sintassi di `.env`, senza collegarti ai servizi:

```bash
python -m translator_bot --check-config
```

Usa il Python della virtualenv con lo stesso percorso mostrato sotto. Questo controllo valida i valori di configurazione; non verifica che token, chiavi o servizi funzionino.

**Windows:**

```powershell
.\.venv\Scripts\python.exe -m translator_bot
```

**Linux / macOS:**

```bash
.venv/bin/python -m translator_bot
```

Mantieni il processo attivo: chiudere il terminale o spegnere il computer interrompe le traduzioni. `Ctrl+C` arresta il bot. Per un funzionamento continuativo eseguilo su una macchina sempre accesa, con un gestore di servizi configurato per usare la cartella del progetto come directory di lavoro.

Sul computer Windows configurato per questo progetto, `bot_watchdog.ps1` riavvia il bot cinque secondi dopo ogni terminazione e l'attività pianificata **Discord Translator Bot Watchdog** lo avvia automaticamente al login. Gli eventi di riavvio vengono scritti in `bot.watchdog.log`; stdout e stderr del bot sono in `bot.stdout.log` e `bot.stderr.log`. Il watchdog gestisce terminazioni ed errori del processo, ma non può correggere un token, un provider o un permesso Discord configurato in modo errato: in quei casi riavvierà il bot ogni cinque secondi finché il problema non viene risolto.

## 5. Collega i canali

Nel server prepara, ad esempio, questi tre canali ed esegui il comando indicato in ciascuno:

| Canale | Comando |
| --- | --- |
| `#italiano` | `/translate lingua:it` |
| `#francais` | `/translate lingua:fr` |
| `#english` | `/translate lingua:en` |

Nell'interfaccia Discord seleziona italiano, francese o inglese dall'elenco dell'opzione `lingua`.

Se Mario scrive `Ciao a tutti!` in `#italiano`, il messaggio originale resta nel canale. Il bot pubblica la traduzione francese in `#francais` e quella inglese in `#english`, con nome `Mario (tradotto)` e avatar di Mario. Scrivere negli altri canali funziona allo stesso modo, in tutte le direzioni.

La lingua configurata è sia la lingua **sorgente presunta** dei messaggi scritti nel canale, sia la lingua **di destinazione** delle traduzioni ricevute. Perciò scrivi nella lingua del canale: il bot non esegue il rilevamento automatico della lingua. Se più canali condividono la stessa lingua, ricevono la stessa traduzione; un canale con la stessa lingua del sorgente riceve direttamente il testo senza chiamare l'API.

Puoi eseguire nuovamente `/translate` per cambiare lingua. Esegui `/remove` nel canale da scollegare: smette di inviare e ricevere traduzioni. Il webhook resta nel canale e verrà riutilizzato alla successiva attivazione; i messaggi già pubblicati restano visibili. Le impostazioni sopravvivono al riavvio. I collegamenti sono sempre limitati allo **stesso server**.

Quando disattivi un canale o ne aggiorni la configurazione, gli invii ancora in attesa riferiti alla vecchia configurazione vengono saltati. `/remove` attende gli eventuali invii già iniziati prima di confermare la disattivazione.

## Comportamento e limiti

- I messaggi vengono ripubblicati automaticamente nei canali testuali configurati, compresi i canali annunci. DM, thread e forum non sono supportati.
- Il bot ignora tutti i messaggi di bot e webhook, compresi i propri: le copie non generano cicli di traduzione.
- Vengono elaborati solo i nuovi messaggi mentre il bot è connesso. Non vengono sincronizzate modifiche, cancellazioni, reazioni, risposte collegate o cronologia precedente.
- Il testo passa al motore di traduzione. Gli allegati vengono condivisi come **URL originali**, senza scaricarli, ricaricarli o eseguire OCR. Le anteprime dipendono da Discord e gli URL firmati possono scadere: non sono una copia permanente del file. Vedi [URL degli allegati Discord](https://docs.discord.com/developers/reference#signed-attachment-cdn-urls).
- Le menzioni nelle copie non inviano notifiche a utenti, ruoli o `@everyone`. La formattazione complessa, i blocchi di codice e i link nel testo possono essere alterati dal motore di traduzione.
- L'invio a più destinazioni e la traduzione verso lingue diverse usano operazioni asincrone con concorrenza limitata. Lo stesso server viene elaborato dallo stesso worker per conservare l'ordine di arrivo; server assegnati allo stesso worker condividono la sua capacità.
- Cache e code sono in RAM. Ogni worker ha la propria coda, limitata da `MAX_QUEUE_SIZE`; se è piena, il nuovo messaggio viene scartato e l'evento appare nei log. Riavvii, errori del provider e permessi mancanti possono causare traduzioni non consegnate: non c'è una coda persistente né una garanzia di consegna. Esegui **una sola istanza** del bot per evitare duplicati.
- Le persone che leggono i canali collegati vedono il testo e gli URL condivisi, anche se non hanno accesso al canale originale. Il provider riceve il testo da tradurre; il bot non gli invia avatar o file allegati.

## Configurazione avanzata

I valori predefiniti sono adatti per iniziare. I limiti numerici e la durata della cache devono essere interi maggiori di zero. Riavvia il bot dopo aver modificato `.env`.

| Variabile | Predefinito | Significato |
| --- | --- | --- |
| `DISCORD_TOKEN` | Obbligatorio | Token del bot Discord. |
| `DISCORD_GUILD_ID` | Vuoto | Server dove registrare i comandi durante i test; vuoto per la registrazione globale. |
| `TRANSLATION_PROVIDER` | `libretranslate` | `libretranslate` oppure `deepl`. |
| `LIBRETRANSLATE_URL` | `http://127.0.0.1:5000` | URL base, senza `/translate`. |
| `LIBRETRANSLATE_API_KEY` | Vuoto | Chiave, se richiesta dall'istanza. |
| `DEEPL_API_KEY` | Vuoto | Obbligatoria quando il provider è DeepL. |
| `DEEPL_API_URL` | `https://api-free.deepl.com` | URL base DeepL, senza `/v2/translate`. |
| `DATABASE_PATH` | `data/channels.sqlite3` | Percorso del database SQLite, relativo alla cartella di avvio. |
| `WEBHOOK_SUFFIX` | ` (tradotto)` | Suffisso del nome, massimo 40 caratteri. Usa virgolette in `.env` per conservare lo spazio iniziale. |
| `TRANSLATION_CONCURRENCY` | `4` | Numero massimo di chiamate di traduzione contemporanee. |
| `CACHE_SIZE` | `1024` | Numero massimo di traduzioni conservate in RAM. |
| `CACHE_TTL_SECONDS` | `600` | Durata della cache in secondi. |
| `MESSAGE_WORKERS` | `4` | Numero di worker per l'elaborazione dei messaggi. |
| `MAX_QUEUE_SIZE` | `200` | Numero massimo di messaggi in attesa per coda di lavoro. |
| `WEBHOOK_CONCURRENCY` | `8` | Numero massimo di invii webhook contemporanei. |
| `LOG_LEVEL` | `INFO` | `INFO`, `WARNING`, `ERROR` oppure `CRITICAL`. |

Esempio di suffisso personalizzato:

```dotenv
WEBHOOK_SUFFIX=" (traduzione automatica)"
```

Il database conserva ID del server, ID del canale, lingua e ID del webhook, senza token dei webhook. La stessa configurazione viene mantenuta anche nel file leggibile `data/channels.json`: viene aggiornato quando attivi, modifichi o rimuovi un canale e viene usato per recuperare la configurazione se il database è vuoto dopo un riavvio o un ripristino. Per fare un backup semplice, arresta il bot e copia la cartella `data`; la cache delle traduzioni non viene salvata. Non rimuovere il volume Docker se vuoi conservare i modelli di LibreTranslate. Per arrestare solo quel servizio usa `docker compose stop`; per riavviarlo usa `docker compose up -d`.

## Verifica e problemi comuni

I test locali si eseguono, con le dipendenze installate, mediante:

```bash
python -m unittest discover -s tests -v
```

Usa il Python della virtualenv, ad esempio `.\.venv\Scripts\python.exe` su Windows o `.venv/bin/python` su Linux/macOS. I test automatizzati non richiedono credenziali e non sostituiscono una prova reale sul tuo server Discord e sul provider scelto.

Per la prova completa configura i tre canali, invia un messaggio in ciascuno e verifica le due copie con avatar, l'assenza di cicli e la conservazione dell'originale. Riavvia il bot per controllare la persistenza. Esegui `/remove` in un canale e verifica che non invii né riceva più nuovi messaggi tradotti.

| Problema | Controllo |
| --- | --- |
| Token mancante o login fallito | Controlla `DISCORD_TOKEN`, il file `.env` e la cartella da cui avvii il bot. |
| `PrivilegedIntentsRequired` / chiusura Gateway 4014 | Abilita Message Content Intent nel Developer Portal e riavvia. |
| I comandi non compaiono | Controlla gli scope d'installazione, `DISCORD_GUILD_ID`, il log di sincronizzazione e aggiorna il client Discord. |
| Comando rifiutato | L'utente deve avere Gestisci canali; il comando deve essere eseguito in un canale testuale del server. |
| Il bot vede solo messaggi che lo menzionano | Verifica Message Content Intent. |
| `403` / permessi mancanti | Controlla i permessi effettivi del bot sul singolo canale e la possibilità di gestire webhook. |
| LibreTranslate non raggiungibile | Controlla `docker compose ps`, i log e `LIBRETRANSLATE_URL`; attendi il download dei modelli. |
| Lingua non disponibile | Controlla l'endpoint `/languages` e che i modelli `it`, `fr`, `en` siano caricati. |
| DeepL `403` o `456` | Controlla la chiave e l'endpoint Free/Pro; `456` indica quota esaurita. |
| `429`, ritardi o coda piena | Controlla i limiti e la capacità del provider; riduci la concorrenza per un servizio con rate limit. Aumentare la coda aumenta l'attesa. |
| Copie doppie | Assicurati che sia in esecuzione una sola istanza del bot. |

Le librerie e le API esterne possono evolvere. I riferimenti al comportamento delle API e alla quota DeepL sono stati verificati il **17 settembre 2026**; la connessione al tuo server e le traduzioni reali richiedono il tuo token e un provider disponibile.
