# Architecture

## The one rule, as code

> Never ask for the book. Ask for the machine that makes the book.

Nothing in this repository produces a specific book. `kdp_factory/booktypes/`
describes *kinds* of books, `kdp_factory/content/templates/` holds the raw
material, and a niche file decides which one gets made. Running the engine twice
with different niches is the normal case, not a special one.

## Flow

```
niche.yaml
   │
   ▼
[1] NicheStation ──────────► 01_niche/niche.json, niche_score.json
   │                                  │
   │                          ┌───────▼────────┐
   │                          │ G1 NicheGate   │  reads the JSON from disk
   │                          └───────┬────────┘
   ▼                                  │ fails ──► STOPPED.md, exit 2
[2] InteriorStation ───────► 02_interior/interior_plan.json + interior.pdf
   │                                  │
   │                          ┌───────▼────────┐
   │                          │ G2 Substance   │  opens the PDF, reads the text
   │                          └───────┬────────┘
   ▼                                  │
[3] CoverStation ──────────► 03_cover/cover_wrap.pdf + cover_proof.pdf
   │                                  │
   │                          ┌───────▼────────┐
   │                          │ G3 PrintReady  │  re-measures both PDFs
   │                          └───────┬────────┘
   ▼                                  │
[4] ListingStation ────────► 04_listing/listing.json, listing.md, keywords.txt
   ▼
[5] UploadStation ─────────► 05_upload/upload_plan.json, upload_run.md
   ▼
[6] ReviewStation ─────────► 06_review/review_checklist.md, report.md
```

## Why stations talk through files

A station writes artifacts and records them in `manifest.json` with a sha256.
The next station reads what it needs. Nothing important is passed only in
memory.

That buys three things:

1. **Gates can be independent.** A gate is handed a `GateInput` of file paths
   and plain facts. It physically cannot see the generator's intent — only the
   artifact a buyer would get.
2. **Any stage can be inspected or re-run** without re-running the ones before
   it.
3. **The run is auditable**: the manifest says what was produced, when, by which
   engine version, against which spec-card version, and what each file hashes to.

## Modules

| Path | Responsibility |
| --- | --- |
| `spec/kdp_spec.yaml` | Every KDP number: thickness, bleed, margins, limits, costs |
| `spec/kdp.py` | Applies them: spine, wrap geometry, gutter, price floors |
| `niche.py` | The niche model and the 10-signal weighted scoring sheet |
| `booktypes/` | Pluggable formats; each returns an `InteriorPlan` |
| `content/` | Template packs, seeded RNG, shared text measure, copy, optional LLM hook |
| `render/` | ReportLab interior and cover renderers; PDF read-back helpers |
| `render/typography.py` | Vendored typefaces, registered once, with a builtin fallback |
| `render/design.py` | Colour worlds and drawn motifs, chosen from the niche |
| `render/identity.py` | One palette and motif per book, shared by both renderers |
| `render/layout.py` | Text primitives both renderers share (wrap, balance, tracking) |
| `assets/fonts/` | The OFL typefaces, so a build needs no network |
| `gates/` | The three gates and the gate framework, with telemetry |
| `stations/` | The six stations |
| `run/` | Build context, manifest, pipeline |
| `upload/` | Upload plan, KDP selector map, Playwright driver |
| `cli.py` | The `kdp` command |
| `.claude/skills/kdp-factory/` | The skill wrapper: the engine, explained to a fresh chat |

## Determinism

`content/rng.py` derives a seed per *stage* from `sha256(run seed, stage name)`.
Stages take sub-streams by name, so adding a draw in one place cannot reshuffle
another. ReportLab runs in invariant mode, so two builds of the same plan are
byte-identical — which is what makes the determinism test meaningful rather than
decorative.

## Where a number lives

If a number is countable, it has exactly one home:

- printing, pricing, geometry → `spec/kdp_spec.yaml`
- the quality bar → `QualityBar` in `config.py`
- when a niche may pass → `NicheGateConfig`
- how a price is derived → `PricePolicy`

The generator and the substance gate share one similarity function
(`content/text.py`) for exactly this reason: when they each had their own, the
generator could pass itself and still fail the gate.
