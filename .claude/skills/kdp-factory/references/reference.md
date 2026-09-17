# KDP Factory — reference

Read this when you need a field's exact name, or when a build stops.

## The ten signals

Scored 0–10, weights sum to 1.0. Four are **risk-shaped**: a high score there is
bad, and the scorer flips them before weighting.

| Signal | Weight | Evidence required by gate 1 | 10 means |
| --- | --- | --- | --- |
| `demand_volume` | 0.18 | **yes** | Lots of people are looking |
| `proven_sales` | 0.14 | **yes** | Top results are demonstrably selling |
| `competition_gap` | 0.12 | no | Incumbents are visibly weak |
| `differentiation` | 0.12 | **yes** | You have a specific, real angle |
| `review_moat` ⚠ | 0.10 | no | Thousands of reviews to outrank (**bad**) |
| `price_headroom` | 0.08 | no | Room between printing cost and market price |
| `format_fit` | 0.08 | no | This format suits this audience perfectly |
| `longevity` | 0.07 | no | Still selling in two years |
| `seasonality` ⚠ | 0.05 | no | Sells in one short window only (**bad**) |
| `trademark_risk` ⚠ | 0.06 | no | The obvious title is somebody's mark (**bad**) |

Gate 1 passes at a weighted total of **6.0** with `demand_volume` at **5.0** or
above and `trademark_risk` at **3.0** or below.

## Full niche schema

```yaml
niche: str                 # required
book_type: str             # required: journal | planner | puzzle
audience: str              # the research note
audience_short: str        # optional; the few words that go on a cover
title: str                 # optional; overrides the proposed title
subtitle: str              # optional; overrides the proposed subtitle
promise: str
keywords_seed: [str]       # three or more, or gate 1 fails
signals:
  <signal>:
    score: float           # 0-10
    evidence: str
    source: str
competitor:
  title: str
  asin: str
  price: float
  reviews: int
  rating: float
  page_count: int
  gaps: [str]              # in the incumbent's own reviews
trademark:
  checked: bool            # false fails gate 1
  source: str              # empty fails gate 1
  checked_on: str
  hits: [str]              # any hit fails gate 1
constraints:
  trim_size: str           # see `kdp spec`
  paper: str               # bw_white | bw_cream | standard_color | premium_color
  target_pages: int        # even, 24-828 depending on paper
  price: float             # optional; pins the price instead of deriving it
options: {}                # book-type options; see `kdp types`
notes: str
```

## Book types and their options

Run `kdp types` for the live list. As shipped:

| Type | Key options | Notes |
| --- | --- | --- |
| `journal` | `target_pages`, `lines_per_page`, `divider_every`, `date_line` | One prompt per page; the prompt count is solved for the target |
| `planner` | `target_pages`, `habits_per_week`, `monthly_overview`, `months` | Two pages per week; 56 focus lines caps it at 56 weeks |
| `puzzle` | `target_pages`, `grid_size`, `words_per_puzzle`, `difficulty`, `solutions_per_page` | Every grid is machine-verified before it ships |

## Failure modes, and the fix

| What you see | What it means | Fix |
| --- | --- | --- |
| `demand_evidence_named` FAIL | A load-bearing signal was scored with no evidence | Ask for the source. Do not invent one |
| `trademark_checked` FAIL | `checked: false`, or no `source` | Have them search USPTO TESS and record the date |
| `keyword_seeds` FAIL | Fewer than three seeds | Ask for more; the listing is written from these |
| `score_threshold` FAIL | Weighted total under 6.0 | The niche is weak. Say so — this is the gate doing its job |
| `repetition_near_duplicate` FAIL | Two content units read alike | Add lines to the template pack, or lower `target_pages` |
| `value_no_empty_content_pages` FAIL | A content page renders blank | A renderer bug; report it rather than working around it |
| `cover_size_matches_page_count` FAIL | Cover built for a different page count | Re-run the build; never patch the PDF |
| `ConfigError: only N of M prompts…` | The pack cannot fill a book this long without repeating | Lower `target_pages`, or add prompts to `content/templates/journal.yaml` |
| `ConfigError: every one of the N focus lines has been used` | A previous planner spent the pack | Add focus lines to `content/templates/planner.yaml` |
| `ConfigError: N puzzles need more themes than are left` | A previous puzzle book used those themes | Add themes to `content/templates/wordlists.yaml` |

## How many books the packs hold

The binding constraint on an imprint is the packs, not the niches. Current
capacity, measured at the point a build actually stops:

| Pack | Holds |
| --- | --- |
| `journal.yaml` | ~4 journals at 109 prompts (all of them share the 204-prompt `core` bank) |
| `planner.yaml` | 2 planners at 56 undated weeks |
| `wordlists.yaml` | 2 puzzle books at 76 puzzles |

Past that, the engine refuses rather than repeating itself. New lines must clear
the same 70% overlap bar the generator uses, against every line already in the
pack — check with `kdp_factory.content.text.similarity` before adding.
| `ConfigError: …disjoint word sets…` | More puzzles than the themes support | Lower `target_pages`, or add themes to `wordlists.yaml` |
| `SpecViolation: …below the $X floor` | A pinned price loses money on every copy | Remove `constraints.price` and let the engine derive it |
| `SpecViolation: title + subtitle is N characters` | Over KDP's 200 | Shorten the subtitle pattern for that book type |

## Commands

```bash
kdp types                          # book types and their options
kdp spec                           # the KDP spec card: spine, bleed, limits, costs
kdp scout niches.csv               # rank many niches before building any
kdp score niche.yaml               # station 1 + gate 1 only, in a second
kdp build niche.yaml               # the whole run
kdp build niche.yaml --avoid output/   # second book in a niche, no repeats
kdp show output/<run>              # what a finished run produced
kdp upload output/<run>            # dry run; --execute opens a browser
kdp gates                          # how often each gate has actually failed
```

Shared options (`--config`, `--output`, `-v`) work before or after the
subcommand.

## What the engine will not do

- Publish. `FORBIDDEN_ACTIONS` in `upload/plan.py` raises if anything aims at
  that button.
- Judge whether the book is any good to read. The substance gate counts,
  compares and verifies presence; it cannot tell you a prompt is boring.
- Tell you the spec card has gone stale. Check `kdp spec`'s `last_verified`
  date against KDP's current documentation before a launch.
