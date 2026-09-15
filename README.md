# KDP Factory

An engine that makes books — not a book.

This is an implementation of **The KDP Factory Prompt Pack**: six stations, three
gates that can fail a build and stop it, and a rule the whole design hangs on —

> Never ask for the book. Ask for the machine that makes the book.

One niche file goes in. A print-ready interior, a full cover wrap sized to the
page count that actually exists, a complete listing pack and a prepared upload
run come out. The engine stops before Publish, on purpose.

```
niche.yaml ─► [1 niche] ─(G1)─► [2 interior] ─(G2)─► [3 cover] ─(G3)─►
              [4 listing] ─► [5 upload plan] ─► [6 your review]
```

## Install

```bash
pip install -e .            # engine + renderers
pip install -e '.[dev]'     # + pytest
pip install -e '.[upload]'  # + playwright, for the browser run
pip install -e '.[llm]'     # + anthropic, for the optional copy/grading hook
```

## Run it

```bash
kdp types                                   # what this factory can build
kdp spec                                    # the KDP spec card, in one screen
kdp scout examples/niches.csv               # rank niches before building any
kdp score examples/gratitude-new-mothers.yaml   # station 1 + gate 1 only
kdp build examples/gratitude-new-mothers.yaml   # the whole run
kdp show  output/<run>                      # what a finished run produced
kdp upload output/<run>                     # dry run; --execute opens a browser
kdp gates                                   # how often each gate has failed
```

Publishing a second book in the same niche? Tell the engine what it already
used, or book two will repeat about a third of book one:

```bash
kdp build niche.yaml --seed 2 --avoid output/
```

A finished run:

```
output/gratitude-journal-for-new-mothers--journal--s0/
├── 01_niche/       niche.json · niche_score.json · niche_report.md
├── 02_interior/    interior_plan.json · ..._interior.pdf · book_type_checks.json
├── 03_cover/       ..._cover_wrap.pdf · ..._cover_proof.pdf · cover_spec.json
├── 04_listing/     listing.json · listing.md · keywords.txt
├── 05_upload/      upload_plan.json · upload_run.md
├── 06_review/      review_checklist.md
├── gates/          g1_niche.json · g2_substance.json · g3_print.json
├── manifest.json   every artifact, with its sha256
└── report.md
```

## The six stations

| # | Station | What it does | What it refuses to do |
| --- | --- | --- | --- |
| 1 | Niche | Scores a niche on 10 weighted signals, each carrying its evidence | Decide whether the score is good enough — gate 1 does that |
| 2 | Interior | Plans every page, then renders a print-ready PDF at trim size | Grade its own work |
| 3 | Cover | Builds the wrap from the page count of the PDF **that exists** | Guess a spine width |
| 4 | Listing | Title, subtitle, description, 7 keywords, 3 categories, price | Improvise at the upload screen |
| 5 | Upload | Writes exactly what to type, and a driver that types it | Click Publish |
| 6 | Review | Hands you a checklist with this run's own numbers | Tick any of it for you |

## The three gates

A gate reads the artifact **from disk** — it is handed file paths, never the
objects the producing station held in memory. That is the mechanical form of
*never let the same step both produce and approve an artifact*.

| Gate | Question | Fails when, for example |
| --- | --- | --- |
| `g1_niche` | Is this niche worth a book at all? | A load-bearing signal has no evidence; trademark unchecked; score under the bar |
| `g2_substance` | Would a buyer feel they got their money's worth? | Padding over the bar; near-duplicate content; a number in the title the book cannot back; a content unit that never reached the page |
| `g3_print` | Will KDP actually print this? | The cover was built for a different page count than the interior has |

Every gate run is logged. `kdp gates` reports the fail rate per gate and says so
when a gate has never failed anything:

```
gate              runs  failed    rate  verdict
g1_niche            12       2     17%  healthy: this gate has caught things and let things through
g2_substance        10       0      0%  never fails — suspect the criteria are too vague to catch anything
```

## What is computed, never estimated

Everything countable lives in [`kdp_factory/spec/kdp_spec.yaml`](kdp_factory/spec/kdp_spec.yaml)
and is applied by [`spec/kdp.py`](kdp_factory/spec/kdp.py):

- spine width = page count × paper thickness — from the PDF's real page count
- cover wrap = 2 × trim + spine + 2 × bleed
- gutter margin, banded by page count; page-count minimum, maximum and parity
- printing cost, the price floor where royalty hits zero, and royalty per copy

Retailer specs change. That file is the single place to change them.

## Determinism

The same niche and the same seed produce a **byte-identical** interior PDF.
Content generation is seeded per stage, so drawing more prompts does not change
what the cover picks, and a run from three months ago can be reproduced exactly.

```bash
kdp build examples/senior-word-search.yaml --seed 7
```

The optional LLM hook is **off by default** for this reason. When enabled it can
sharpen copy and give the substance gate a second opinion — advisory unless you
explicitly let a model fail a build.

## Front ends

Two ways in, besides the terminal.

**The `/kdp-factory` skill** (`.claude/skills/kdp-factory/`) turns "build the next
book" into a complete instruction in a chat that has never seen this project. It
carries the interview, the niche schema, the failure modes and one rule it will
not break: fix the input, never the gate.

**The Niche Proof Sheet** — a page you can open from any Claude app, phone
included:

<https://claude.ai/artifact/AntbVZq4G9VE8PGrXBCur8>

It composes the niche file and shows **gate 1's verdict as you type** — the same
ten weighted signals, the same thresholds, the same pass/fail reasons the build
will print — plus the spine, wrap and price maths from the spec card. It cannot
build (no Python in a sandbox), so it hands off two ways: copy the YAML, or save
the sheet and ask Claude for it by name where the engine lives. The skill reads
it back out of the page's own store and runs the build.

```
research on a phone  →  Niche Proof Sheet  →  saved
                                               ↓
                        "build the nurses niche from the proof sheet"
                                               ↓
                             /kdp-factory  →  kdp build  →  a book
```

## Book types

`journal`, `planner` and `puzzle` ship with the engine. They are plugins: a new
format is a module and a decorator, not a fork.

```python
from kdp_factory.booktypes.base import BookType, InteriorPlan, register

@register
class ColouringBookType(BookType):
    key = "colouring"
    label = "Colouring book"

    def plan(self, niche, config, rng, options=None) -> InteriorPlan: ...
    def titles(self, niche, rng): ...
    def verify(self, plan): ...          # what code can settle about this format
```

See [`docs/adding-a-book-type.md`](docs/adding-a-book-type.md).

## Tests

```bash
python -m pytest -q
```

The suite includes the tests that matter most for a factory: the spec math, the
byte-for-byte determinism of a build, and — for each gate — a build that is
deliberately broken in one specific way, asserting the right check goes red.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — station-by-station reference
- [`docs/niche-file.md`](docs/niche-file.md) — the input format and the 10 signals
- [`docs/gates.md`](docs/gates.md) — every check, and what fails it
- [`docs/adding-a-book-type.md`](docs/adding-a-book-type.md)
- [`docs/prompt-pack-mapping.md`](docs/prompt-pack-mapping.md) — which prompt produced which module

## The honest part

Automation makes a good process fast. It makes a bad process fast too. This
engine multiplies output; it does nothing to the number of people who want what
you make. Point it at a dead niche and it will produce dead books very
efficiently.

No income claims. Publishing outcomes depend on your niche, your quality and
your market. Verify print specs against KDP's current documentation.
