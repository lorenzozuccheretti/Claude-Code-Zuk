# KDP-Intelligence-Engine (`kdp_intel`)

Motore multi-agente per saggistica italiana di fascia premium su Amazon KDP:
ricerca di mercato, analisi delle recensioni dei concorrenti, scrittura capitolo
per capitolo con RAG, fact-checking che blocca la stampa, impaginazione PDF
conforme KDP. Vive accanto a `kdp_factory` (libri low-content) e ne riusa la
scheda tecnica KDP (`kdp_factory/spec/kdp_spec.yaml`) e i font.

**Costo predefinito: zero.** Ogni capacità ha un'opzione gratuita, e i
servizi a pagamento entrano in gioco solo se ne imposti le credenziali.

| Serve | Gratis (predefinito) | A pagamento (facoltativo) |
|---|---|---|
| Modello linguistico | hand-off a questa sessione Claude Code o a una chat; Gemini free tier; OpenRouter `:free`; Ollama locale | Claude API |
| Ricerca web | Tavily (chiave gratuita, 1.000 ricerche/mese, senza carta); DuckDuckGo (senza chiave, spesso bloccato dal cloud) | SerpAPI |
| Domanda | autocompletamento Amazon.it e Google (senza chiave) | DataForSEO; Helium 10 (export CSV) |
| Andamento | CSV esportato da trends.google.it | SerpAPI Trends |
| Concorrenti, BSR, recensioni | pagine Amazon.it salvate dal tuo browser | SerpAPI, Bright Data, Apify |
| Forum | app Reddit "script" (gratuita) | — |
| Archivio vettoriale, embedding | ChromaDB locale, embedding hashing o multilingue locale | Pinecone |
| Impaginazione, copertina | Typst / WeasyPrint; Canva (piano gratuito) con il modello generato da `kdpi cover-spec` | — |

```
                 ┌────────────── vector store (Chroma | Pinecone | memoria) ◄──────────────┐
                 │                 BM25 + vettori, filtri per autorevolezza e data          │
                 ▼                                                                          │
ingest ─► Analyst ─► Review Miner ─► Architect ─► Writer ─► Fact-Checker ─┬─► Typesetter ─► PDF
 fonti     autocompl. pagine Amazon    persona      RAG       5 controlli  │   Typst |      │
 ufficiali Trends CSV salvate / CSV    + indice     + lint    + verifica   │   WeasyPrint   ▼
 Reddit    Tavily                      (pausa per              web          │         cover-spec
                                        approvarlo)   ▲                    │         → Canva
                                                      └── revisione ◄──────┤ (max N)
                                                                           └─► bloccato
```

Il grafo è un `StateGraph` LangGraph (`kdp_intel/graph.py`). Gli agenti sono
oggetti con le dipendenze iniettate; il grafo sposta lo stato e decide il
percorso. Ogni nodo salva il proprio risultato nella cartella di lavoro
(`.kdp_intel/<slug>/`), quindi una run si può ispezionare e riprendere: i
capitoli già verificati non vengono riscritti.

## Gli agenti

| Agente | File | Cosa fa | Cosa NON fa |
|---|---|---|---|
| **Analyst** | `agents/analyst.py` | Trova fonti fresche sui domini ufficiali, misura Trends (geo IT), volumi Amazon, voti, recensioni e BSR dei concorrenti; calcola il punteggio in codice | Non chiede numeri a un modello. Senza una misura della domanda il punteggio è 0, non una stima |
| **Review Miner** | `agents/review_miner.py` | Prende le recensioni da 1-3 stelle, il modello propone i temi e li assegna, **il codice conta**. Ogni citazione deve comparire parola per parola nella recensione | Non inventa citazioni: quelle false vengono scartate e contate |
| **Architect** | `agents/architect.py` | Persona dai thread e dalle recensioni; indice con struttura obbligatoria (Introduzione, 3 Parti, Conclusione) validata in codice | — |
| **Writer** | `agents/writer.py` | Un capitolo alla volta, solo dalle evidenze recuperate; ogni cifra, data o norma porta il marcatore `[[S:id]]`; lint su frasi vietate, fonti sconosciute, tabelle malformate, presenza di tabella + caso pratico + checklist | Non può citare fonti che non gli sono state mostrate |
| **Fact-Checker** | `agents/fact_checker.py` | Vedi sotto | Non accetta "supportato" senza una citazione verbatim dalla fonte |

### I cinque controlli del Fact-Checker

Ogni frase o riga di tabella con un numero, una percentuale, un importo, una
data o un riferimento normativo è un'*affermazione verificabile*, e passa i
controlli in quest'ordine (dal più economico e certo):

1. **citata**: ha almeno un `[[S:id]]`;
2. **in archivio**: la fonte citata è nel vector store;
3. **autorevole**: cifre e norme poggiano su fonti di livello 1-2; la stampa
   più vecchia di `max_source_age_days` non vale come prova;
4. **numeri**: ogni numero dell'affermazione compare nel testo della fonte
   (confronto di stringhe, nessun modello; gestisce `1.401,53`, `16,4%`,
   `652mila`, `1,2 milioni`);
5. **implicazione**: il modello giudica se la fonte afferma la cosa, e deve
   citarla; una citazione che non è una sottostringa esatta dell'evidenza
   equivale a nessun supporto.

Le affermazioni non citate o non supportate vengono cercate sul web nei
domini ufficiali (`research.verify_domains`), le pagine trovate vengono
indicizzate e la fonte da citare viene suggerita al Writer. L'affermazione
resta comunque fallita: è il Writer a doverla correggere, e il passaggio
successivo deve dimostrarla. Dopo `max_revisions` tentativi il capitolo è
**bloccato** e il libro non viene impaginato (`--draft-proof` produce una
bozza lo stesso).

### Livelli di autorevolezza (`authority.py`)

| Livello | Esempi | Uso |
|---|---|---|
| 1 | normattiva.it, gazzettaufficiale.it, agenziaentrate.gov.it, inps.it, istat.it, `*.gov.it`, eur-lex | Norme, cifre, procedure. Non scadono per età |
| 2 | Il Sole 24 Ore, FiscoOggi, IPSOA, PMI.it, Altroconsumo, Money.it… | Cifre e prassi, entro la finestra di freschezza |
| 3 | Reddit, Quora, recensioni, siti non classificati | Solo linguaggio e problemi del lettore, mai cifre |

## RAG

- **Ingestione** (`rag/ingest.py`): HTML ripulito da menu e banner, PDF via
  `pypdf`, thread Reddit; chunk di circa 1.100 caratteri rispettando i
  paragrafi. Ogni fonte riceve un id stabile e leggibile (`istat-102728`) e
  finisce nel manifest `sources.json`, da cui escono note e bibliografia
  qualunque sia il backend vettoriale. La data viene letta dai meta tag o
  dalle formule italiane ("Ultimo aggiornamento: 14 febbraio 2025").
- **Store** (`rag/store.py`): `ChromaStore` (default, locale e persistente),
  `PineconeStore` (serverless, `pip install -e '.[pinecone]'`), `MemoryStore`
  (test). Filtri comuni: livello massimo, fonti specifiche, data minima (solo
  per stampa e community).
- **Ricerca ibrida** (`rag/lexical.py`): un ampio insieme di candidati
  vettoriali viene riordinato con BM25 sull'intero archivio, con stemming
  italiano leggero, e le due classifiche sono fuse con reciprocal rank fusion.
- **Embedding** (`rag/embeddings.py`): `hashing` (default, nessun download,
  deterministico) oppure `--embedder multilingual`
  (`paraphrase-multilingual-MiniLM-L12-v2`, `pip install -e '.[embeddings]'`)
  per il recupero semantico.

## Fornitori di dati (`providers/`)

Gratuiti (`providers/free.py`, scelti per primi):

| Dove | Servizio | Fornisce |
|---|---|---|
| `TAVILY_API_KEY` | Tavily (gratis, senza carta) | Ricerca web; `site:dominio` diventa `include_domains` |
| nessuna chiave | DuckDuckGo HTML | Ricerca web di riserva; dal cloud risponde spesso con una verifica anti-bot, e allora la run lo annota invece di fermarsi |
| nessuna chiave | Autocompletamento Amazon.it e Google | Segnale di domanda: varianti a coda lunga che le persone digitano davvero. È un indicatore, non un volume, e il report lo dice |
| `research.trends_dir` | Google Trends, CSV esportato | Andamento a 12 mesi (Italia), una colonna per termine |
| `research.amazon_pages_dir` | Pagine Amazon.it salvate (Ctrl+S) | Risultati di ricerca (titolo, voto, recensioni, prezzo), BSR dalle schede prodotto, recensioni dalle pagine filtrate per 1-3 stelle |
| `research.review_csv` | CSV di recensioni | Recensioni copiate o esportate a mano |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | App Reddit "script" (gratis) | Thread dei subreddit italiani. Dal cloud Reddit rifiuta l'accesso anonimo |

Ricerche web e autocompletamento sono salvati in `cache/`: una run ripetuta
non consuma quota.

A pagamento (facoltativi, entrano solo con le credenziali):

| Variabile d'ambiente | Servizio | Fornisce |
|---|---|---|
| `SERPAPI_API_KEY` | SerpAPI | Google.it, Google Trends (`geo=IT`), ricerca Amazon.it |
| `APIFY_TOKEN`, `APIFY_REVIEWS_ACTOR` | Apify | Recensioni Amazon.it tramite un actor a scelta |
| `BRIGHTDATA_API_TOKEN`, `BRIGHTDATA_ZONE` | Bright Data Web Unlocker | Pagine che rifiutano HTTP semplice, BSR, recensioni |
| `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | DataForSEO | Volumi di ricerca Amazon e Google per l'Italia |
| `PINECONE_API_KEY`, `PINECONE_INDEX` | Pinecone | Vector store gestito |

Un servizio senza credenziali viene disattivato e la mancanza compare nel log
della run. Nessun dato viene inventato al suo posto.

## Modelli linguistici (`llm.py`, `llm_free.py`)

Gli agenti chiedono sempre un oggetto JSON conforme a uno schema pydantic, quindi
qualunque backend capace di restituire JSON va bene. Si sceglie con `--llm`:

| `--llm` | Costo | Note |
|---|---|---|
| `handoff` | zero | Ogni richiesta diventa un file `llm_cache/<id>.request.md` (istruzioni, evidenze, schema). Chi risponde (questa sessione Claude Code, o tu incollandola in una chat) scrive `<id>.json`; `kdpi answer` lo valida. La run successiva riparte da lì |
| `gemini` | zero (free tier) | `GEMINI_API_KEY` da aistudio.google.com, senza carta. Modello predefinito `gemini-3.8-flash`, ritmo limitato al tetto al minuto; a quota giornaliera esaurita si ferma e riprende il giorno dopo. Sul piano gratuito Google può usare i contenuti inviati per migliorare i suoi prodotti: va bene per fonti pubbliche, non per dati privati |
| `openrouter` | zero (modelli `:free`) | `OPENROUTER_API_KEY`; modello predefinito `openrouter/free` |
| `ollama` | zero (hardware tuo) | `OLLAMA_BASE_URL`, modello locale a scelta con `--model` |
| `claude` | a pagamento | Claude API via SDK ufficiale: streaming, output strutturato, fallback sui rifiuti |
| `auto` | — | `gemini` se c'è `GEMINI_API_KEY`, altrimenti `handoff` |

Ogni risposta è salvata in `llm_cache/` con l'hash di (schema, istruzioni,
richiesta). Una richiesta già risposta non viene mai rifatta, quindi una quota
esaurita non fa perdere nulla e una run interrotta riparte dove si era
fermata. Le risposte vengono validate contro lo schema; se una non è conforme,
il modello riceve un tentativo di correzione con l'errore.

## Impaginazione (`typeset/`)

Due motori sullo stesso modello a blocchi (paragrafo, titolo, elenchi,
checklist, tabella, riquadro):

- **Typst** (default, `typeset/templates/kdp.typ`): il testo dello scrittore
  non viene mai incollato nel markup: ogni porzione diventa una stringa
  letterale, quindi `#`, `*`, `//` in un URL o un trattino iniziale non
  possono alterare l'impaginato.
- **WeasyPrint**: HTML + CSS Paged Media (`string-set`, `@page :blank`,
  `float: footnote`, `target-counter` per indice e indice analitico).

Entrambi producono: pagina del titolo, pagina di copyright con data di
aggiornamento dei dati e disclaimer, sommario, pagine delle Parti sul
recto, capitoli che iniziano sempre su pagina dispari, verso bianchi senza
testatina né folio, testatine (titolo del libro sul verso, capitolo sul
recto), numero di pagina al piede, note con la fonte di ogni dato, «Fonti e
riferimenti» raggruppate per livello e «Indice analitico» dai termini del
progetto. Corpo Lora 10,5 pt con interlinea circa 1,3; titoli Karla.

La gola dipende dal numero di pagine, che a sua volta dipende dalla gola:
l'impaginazione pianifica dal numero di parole, compila, e ricompila solo se
il conteggio reale cade in un'altra fascia di gola KDP. Il preflight
(`typeset/preflight.py`) controlla il PDF che esiste davvero: formato di ogni
pagina, numero di pagine pari e nei limiti KDP, margine interno rispetto alla
fascia del conteggio reale, font incorporati.

## Uso

```bash
pip install -e '.[intel,dev]'
kdpi providers                                    # cosa è attivo, gratis e no
P=intel_projects/successione-2026.yaml
kdpi ingest   $P                                  # fonti nel vector store (una volta)
kdpi research $P                                  # punteggio delle nicchie
kdpi run      $P --stop-after architect           # persona e indice, poi pausa
#   rivedi .kdp_intel/successione-2026/04_outline.json, modificalo se serve
kdpi run      $P                                  # scrittura, verifica, PDF
kdpi pending  $P                                  # (handoff) richieste da rispondere
kdpi answer   $P <id> risposta.json               # (handoff) valida e archivia
kdpi cover-spec  $P                               # misure e modello per Canva
kdpi cover-check $P copertina.pdf                 # controlla il PDF esportato da Canva
```

Opzioni comuni: `--llm`, `--model`, `--store chroma|pinecone|memory`,
`--embedder`, `--offline fixtures.json`, `--today AAAA-MM-GG`.

Uscita di `kdpi run`: `0` stampabile, `2` bloccato o solo bozza, `3` in attesa
di risposte (handoff), `4` indice pronto per l'approvazione.

Il libro non viene dichiarato stampabile se autore, editore o titolo sono
segnaposto ("da definire").

## Autopilota: dal tema al PDF senza passaggi manuali

```bash
kdpi autopilot intel_projects/autopilot.yaml --llm handoff   # o --llm gemini
```

| Fase | Cosa fa | Chi decide |
|---|---|---|
| 1. Raccolta | per ogni seme del profilo legge cosa digitano gli utenti nella ricerca **Libri** di Amazon.it (seme, seme + spazio, seme + ogni lettera) e su Google | codice |
| 2. Proposta | un modello raggruppa le frasi in temi di libro; le keyword non digitate da nessuno vengono scartate | modello, filtrato dal codice |
| 3. Controllo keyword | la keyword deve comparire nei suggerimenti Libri di Amazon.it e avere una coda lunga (`min_longtail`) | codice |
| 4. Controllo concorrenza | catalogo IBS.it: titoli degli ultimi 2 anni per la keyword (`max_recent_titles`) | codice |
| 5. Controllo fonti | ricerca web per dominio ufficiale + notizie recenti; le pagine vengono scaricate e classificate; servono `min_official_sources` pagine ufficiali che usano tutte le parole della keyword e `min_fresh_sources` pagine recenti | codice |
| 6. Scelta | punteggio 0-10 = 40% domanda + 30% spazio sul mercato + 30% attualità, solo tra i temi che superano tutti i controlli; se nessuno passa, si ferma (`no_topic`) e spiega perché | codice |
| 7. Progetto | scrive `intel_projects/auto/<slug>.yaml` con keyword e fonti (solo livello 1-2, scaricate) | codice |
| 8. Indice | persona e indice dalle evidenze | modello |
| 9. Scheda Amazon | titolo, sottotitolo, descrizione, 7 keyword nascoste, 2 categorie; regole KDP verificate in codice (`07_listing.md`) | modello, verificato dal codice |
| 10. Libro | scrittura, fact-check con revisioni, impaginazione, guida copertina Canva | grafo esistente |

Perché IBS e non Amazon per la concorrenza: dal cloud Amazon.it risponde 503
alle pagine di ricerca, mentre l'autocompletamento Amazon (la domanda) resta
raggiungibile. IBS vende gli stessi editori italiani, con anno, prezzo e
valutazioni; i titoli solo-KDP non compaiono. Se salvi pagine Amazon in
`research.amazon_pages_dir` o configuri Apify, l'analista le usa in più.

**Ricerca web gratuita in hand-off.** Senza chiave Tavily, ogni ricerca
diventa una richiesta `webresults-*` a cui risponde l'agente con il proprio
strumento di ricerca (la skill `.claude/skills/kdp-autopilot` dice come). Gli
URL non vengono creduti sulla parola: ogni pagina viene scaricata e
classificata, e una pagina inesistente semplicemente non entra
nell'archivio. Con `TAVILY_API_KEY` la ricerca è diretta e senza hand-off.

**Automazione completa.** In hand-off l'autopilota si ferma a ogni domanda
(uscita `3`); la skill `kdp-autopilot` fa rispondere Claude e rilanciare fino
alla fine, e può girare come routine pianificata. Con `GEMINI_API_KEY`
(gratis) il comando va dall'inizio alla fine da solo, ma per la ricerca web
serve allora `TAVILY_API_KEY`.

Uscita di `kdpi autopilot`: `0` libro pronto (anche come bozza se mancano
autore o editore), `3` in attesa di risposte, `5` nessun tema supera i
controlli, `2` libro bloccato.

## Copertina con Canva (gratis)

Il dorso dipende dal numero di pagine, quindi la copertina si fa dopo l'interno
definitivo.

1. `kdpi cover-spec PROGETTO` legge `05_interior/interior.pdf` e scrive in
   `06_cover/`: `cover_spec.json` (misure in pollici e mm, dorso, area del
   codice a barre), `cover_guide.pdf` e `cover_guide.png` (rosso: abbondanza
   da rifilare; blu: taglio e pieghe del dorso; verde: zona sicura per i
   testi; grigio: area del codice a barre, da lasciare vuota).
2. In Canva: "Crea un design" → "Dimensioni personalizzate" in pollici, con le
   misure del file. Carica `cover_guide.png` come primo livello, costruisci la
   copertina sopra, poi elimina la guida.
3. Scarica come "PDF per la stampa" **senza** segni di taglio e di
   smarginatura: KDP vuole l'abbondanza dentro le misure, non segni intorno.
4. `kdpi cover-check PROGETTO copertina.pdf` controlla pagina unica e misure
   esatte, e riconosce un'esportazione con i segni di taglio.

Testo sul dorso solo sopra le 100 pagine (regola KDP, nella scheda tecnica).

## Limiti noti

- Amazon richiede spesso l'accesso per le pagine delle recensioni: con Bright
  Data il parser HTML può tornare vuoto. In quel caso conviene usare un actor
  Apify che gestisce l'accesso, oppure un CSV (`research.review_csv`).
- La copertura dell'endpoint Amazon di DataForSEO varia per paese: se
  l'Italia non è coperta, il task lo segnala e la run lo riporta, invece di
  usare zero.
- L'embedder `hashing` è lessicale. Per domande parafrasate serve
  `--embedder multilingual`.
- La scheda prodotto (descrizione, keyword, categorie, prezzo in euro) non è
  ancora generata da `kdp_intel`; la scheda tecnica KDP del repository ha i
  costi di stampa solo per amazon.com in dollari.
- `cover-check` non misura la risoluzione delle immagini dentro il PDF: in
  Canva usa immagini ad almeno 300 dpi alla dimensione di stampa.
- Brocardi.it è usato solo per il testo degli articoli del Codice civile:
  note, massime e commenti della pagina vengono scartati, perché sono
  opinioni (una nota all'art. 542 attribuisce al coniuge 1/3 dove l'articolo
  dice un quarto).
- Con l'hand-off ogni richiesta ferma la run: un libro di 12 capitoli richiede
  decine di cicli rispondi → rilancia. Con Gemini free tier il ciclo è
  automatico.
