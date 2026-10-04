# KDP-Intelligence-Engine (`kdp_intel`)

Motore multi-agente per saggistica italiana di fascia premium su Amazon KDP:
ricerca di mercato, analisi delle recensioni dei concorrenti, scrittura capitolo
per capitolo con RAG, fact-checking che blocca la stampa, impaginazione PDF
conforme KDP. Vive accanto a `kdp_factory` (libri low-content) e ne riusa la
scheda tecnica KDP (`kdp_factory/spec/kdp_spec.yaml`) e i font.

```
                 ┌────────────── vector store (Chroma | Pinecone | memoria) ◄──────────────┐
                 │                 BM25 + vettori, filtri per autorevolezza e data          │
                 ▼                                                                          │
ingest ─► Analyst ─► Review Miner ─► Architect ─► Writer ─► Fact-Checker ─┬─► Typesetter ─► PDF
 fonti     SerpAPI    Apify /          persona      RAG       5 controlli  │   Typst |
 ufficiali Trends     Bright Data /    + indice     + lint    + verifica   │   WeasyPrint
 Reddit    DataForSEO CSV              validato               web          │
           Helium 10                                  ▲                    │
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

| Variabile d'ambiente | Servizio | Fornisce |
|---|---|---|
| `SERPAPI_API_KEY` | SerpAPI | Google.it (`gl=it`, `hl=it`), Google Trends (`geo=IT`), ricerca Amazon.it |
| `APIFY_TOKEN`, `APIFY_REVIEWS_ACTOR` | Apify | Recensioni Amazon.it tramite un actor a scelta (`owner~name`); input configurabile, campi di output letti tramite alias |
| `BRIGHTDATA_API_TOKEN`, `BRIGHTDATA_ZONE` | Bright Data Web Unlocker | Pagine che rifiutano HTTP semplice, BSR "Posizione nella classifica Bestseller", recensioni HTML |
| `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | DataForSEO | Volumi di ricerca Amazon (Labs) e Google Ads per l'Italia (`location_code` 2380) |
| `research.helium10_csv` | Helium 10 | Export CSV di Cerebro/Magnet: Helium 10 non ha un'API pubblica per questi strumenti |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit | OAuth app-only; senza, JSON pubblico (soggetto a limiti) |
| `PINECONE_API_KEY`, `PINECONE_INDEX` | Pinecone | Vector store gestito |
| credenziali Anthropic | Claude | Modelli per Review Miner, Architect, Writer e Fact-Checker |

Un servizio senza credenziali viene disattivato e la mancanza compare nel log
della run. Nessun dato viene inventato al suo posto.

Claude è chiamato tramite l'SDK ufficiale (`kdp_intel/llm.py`): richieste in
streaming, output strutturato (`output_config.format` con JSON schema
generato dai modelli pydantic), prompt di sistema in cache, `effort` per
compito, e il fallback lato server sui rifiuti (`fallbacks: "default"`).
Modello predefinito `claude-opus-5-5`, configurabile con `--model`.

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
kdpi providers                                        # quali servizi sono attivi
kdpi ingest   intel_projects/successione-2026.yaml    # fonti nel vector store
kdpi research intel_projects/successione-2026.yaml    # punteggio delle nicchie
kdpi mine     intel_projects/successione-2026.yaml    # lacune dei concorrenti
kdpi run      intel_projects/successione-2026.yaml    # tutto, fino al PDF
kdpi check    intel_projects/successione-2026.yaml --chapter 3
kdpi typeset  intel_projects/successione-2026.yaml --engine weasyprint
```

Opzioni comuni: `--store chroma|pinecone|memory`, `--embedder`,
`--offline fixtures.json` (tutti i fornitori sostituiti da un file),
`--today AAAA-MM-GG` (data fissa per run riproducibili), `--model`.

Uscita di `kdpi run`: `0` libro stampabile, `2` bloccato o solo bozza.

## Limiti noti

- Amazon richiede spesso l'accesso per le pagine delle recensioni: con Bright
  Data il parser HTML può tornare vuoto. In quel caso conviene usare un actor
  Apify che gestisce l'accesso, oppure un CSV (`research.review_csv`).
- La copertura dell'endpoint Amazon di DataForSEO varia per paese: se
  l'Italia non è coperta, il task lo segnala e la run lo riporta, invece di
  usare zero.
- L'embedder `hashing` è lessicale. Per domande parafrasate serve
  `--embedder multilingual`.
- Copertina e scheda prodotto non sono ancora generate da `kdp_intel`.
  `kdp_factory` ha le stazioni per farlo (`stations/s3_cover.py`,
  `s4_listing.py`), ma non sono collegate a questo motore.
