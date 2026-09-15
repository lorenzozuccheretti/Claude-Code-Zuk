---
name: kdp-factory
description: Build a print-ready KDP book with the KDP Factory engine — interior PDF, full cover wrap, listing pack and a prepared upload run, with three gates that can stop the build. Use when the user wants to build, publish or plan a low-content book (guided journal, undated planner, word search puzzle book), score or scout a niche for KDP, work out a spine width, trim or list price, or says "build the next book", "run the factory", or hands over a niche file or a niche CSV.
---

# KDP Factory

Turn "build the next book" into a finished package: a print-ready interior, a
cover wrap sized to the page count that actually exists, a complete listing, and
an upload run that stops before Publish.

**The engine does the building. Your job here is the input** — because the
input is where builds die. Gate 1 rejects a niche scored on hunches, and it is
right to.

## 1. Find the engine

```bash
kdp --version || pip install -e .    # from the repo root
kdp types                            # what this factory can build
```

If `kdp` is missing and there is no repo, the engine lives at
`github.com/lorenzozuccheretti/Claude-Code-Zuk`. Do not reimplement any of it in
the chat — particularly not spine width, printing cost or page maths. Those live
in `kdp_factory/spec/kdp_spec.yaml` and are the whole point.

## 2. Is there already a sheet waiting?

The **Niche Proof Sheet** is the companion page for composing a niche away from
the keyboard — on a phone, in a meeting, wherever the research happens:

https://claude.ai/artifact/AntbVZq4G9VE8PGrXBCur8

When the user says a niche is "in the proof sheet", "saved from my phone", or
names one by slug, read it instead of interviewing them again:

```
Artifact  action: read_db  url: <the sheet's URL>  db_op: list  collection: niches
```

Each document carries `yaml` (the finished niche file), `file_name`, `score` and
`gate1_passes`. Write `yaml` to `niches/<file_name>` verbatim and go to step 4 —
they already did the thinking; do not re-litigate their scores. Their words in
the evidence fields are theirs to keep.

If `gate1_passes` is false, say which check is red before building, rather than
running a build you know will stop.

## 3. Interview for the niche, evidence first

A niche file is not a form to fill in politely. **Every load-bearing signal has
to name where the number came from**, or gate 1 stops the build:

| Signal | Needs evidence | Ask |
| --- | --- | --- |
| `demand_volume` | **yes** | "What search volume did you see, and in which tool, on what date?" |
| `proven_sales` | **yes** | "What are the BSRs of the top three results?" |
| `differentiation` | **yes** | "What will this book do that the top ten do not?" |
| `competition_gap` | recommended | "What do the incumbent's 1-2 star reviews complain about?" |
| `trademark_risk` | — | "Have you checked the title phrase on USPTO TESS? When?" |

If the user cannot answer the first three, **say so plainly and stop**. A niche
you cannot evidence is one the gate will reject in four seconds; guessing the
numbers to get past it defeats the only mechanism that protects them from
publishing into a dead niche. Offer to help them gather it instead.

Ask for the rest conversationally: audience, the promise in one line, three or
more keyword seeds, the incumbent's title/price/review count and its named
weaknesses, trim size, paper, target page count.

`references/reference.md` has all ten signals with weights, the full niche
schema, and every failure mode with its fix. Read it when you need a field's
exact name or a build stops.

Composing on a phone is easier than dictating a YAML file: point the user at the
proof sheet above when they have research to enter and no terminal in front of
them.

## 4. Write the niche file

Copy the shape of `examples/gratitude-new-mothers.yaml` and fill it from the
interview. Keep the user's own words in `evidence:` — that string is what they
will read in six months when they wonder why they built this.

```yaml
niche: "Gratitude journal for new mothers"
book_type: journal                # journal | planner | puzzle
audience: "first-time mothers in the first year"
audience_short: "first-time mothers"     # used on the cover
promise: "Five quiet minutes at the end of a day that had none."
keywords_seed: ["gratitude journal for moms", "new mom gift journal", "..."]
signals:
  demand_volume: { score: 7.5, evidence: "1,900/mo", source: "Helium 10, 2026-01-14" }
  # ... the other nine
competitor:
  title: "..."
  price: 9.99
  gaps: ["pages bleed through", "prompts repeat every week"]
trademark: { checked: true, source: "USPTO TESS", checked_on: "2026-01-15", hits: [] }
constraints: { trim_size: "6x9", paper: "bw_cream", target_pages: 120 }
```

Score it before building — it costs a second and fails fast (the proof sheet
shows the same verdict live, before a file exists):

```bash
kdp score niches/my-niche.yaml
```

## 5. Build

```bash
kdp build niches/my-niche.yaml --config engine.yaml
```

Three gates print PASS/FAIL line by line. Then read the two artifacts that
matter and **tell the user what is in them**, rather than just reporting success:

- `04_listing/listing.md` — the title, the seven keywords, the price and the
  royalty per copy. Quote the title and the price back to them.
- `06_review/review_checklist.md` — the handover.

Publishing a second book in the same niche? Add `--avoid output/`, or book two
repeats about a third of book one.

## 6. When a gate fails

A red gate is the engine working. The run directory gets a `STOPPED.md` naming
the failing check and its reason.

**Fix the input, not the gate.** Never edit `quality:` or `niche_gate:`
thresholds to turn a build green — a gate you loosen to get past it has stopped
being a gate, and the user loses the one thing standing between them and fifty
padded books. Say that out loud if they ask you to lower a threshold.

The three you will actually meet:

- **`demand_evidence_named`** — a signal was scored with no evidence. Go back
  and ask for the source.
- **`repetition_near_duplicate`** — two pieces of content read alike. The
  template pack is thin for this niche; add lines to
  `kdp_factory/content/templates/`, or lower `target_pages`.
- **`cover_size_matches_page_count`** — the cover was built for a different page
  count than the interior has. Re-run the build rather than patching either
  file.

## 7. Hand it over

The engine stops before Publish, by design, and so do you. Do not offer to
publish, and do not tick anything on the review checklist for them: opening the
real PDF, looking at the cover at 100% and reading the listing as a stranger are
the parts no machine in this repo can do.

`kdp upload output/<run>` prints the dry run — every field it would type and the
files it would upload. `--execute` opens a browser where **they** log in, and it
still stops at Publish.
