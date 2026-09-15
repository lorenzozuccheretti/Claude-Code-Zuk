# The niche file

The engine's only input. One YAML file per niche; everything in it is evidence
you collected.

```yaml
niche: "Gratitude journal for new mothers"
book_type: journal                 # journal | planner | puzzle

audience: "first-time mothers in the first year, usually buying at 11pm"
audience_short: "first-time mothers"   # optional; used on the cover
promise: "Five quiet minutes at the end of a day that had none."

keywords_seed:                     # the listing is written from these
  - "gratitude journal for moms"
  - "new mom gift journal"
  - "postpartum journal for mothers"

signals:
  demand_volume:
    score: 7.5                     # 0-10
    evidence: "1,900 monthly searches plus 4 autocomplete variants"
    source: "Helium 10 Magnet, 2026-01-14"
  # ... the other nine

competitor:
  title: "The Gratitude Journal for Moms"
  asin: "B0XXXXXXX"
  price: 9.99
  reviews: 412
  gaps:                            # in the incumbent's own reviews
    - "pages bleed through"
    - "prompts repeat every week"

trademark:
  checked: true
  source: "USPTO TESS"
  checked_on: "2026-01-15"
  hits: []

constraints:
  trim_size: "6x9"
  paper: "bw_cream"                # bw_white | bw_cream | standard_color | premium_color
  target_pages: 120
  price: 8.99                      # optional: pins the price instead of deriving it

options:                           # book-type options; see `kdp types`
  lines_per_page: 13
```

## The ten signals

Weights are built in and sum to 1.0. Four are **risk-shaped** — a high score
there is bad, and the scorer flips them before weighting.

| Signal | Weight | 10 means |
| --- | --- | --- |
| `demand_volume` | 0.18 | Lots of people are looking |
| `proven_sales` | 0.14 | The top results are demonstrably selling |
| `competition_gap` | 0.12 | The incumbents are visibly weak |
| `differentiation` | 0.12 | You have a specific, real angle |
| `review_moat` ⚠ | 0.10 | Thousands of reviews to outrank (**bad**) |
| `price_headroom` | 0.08 | Lots of room between printing cost and market price |
| `format_fit` | 0.08 | This format suits this audience perfectly |
| `longevity` | 0.07 | Still selling in two years |
| `seasonality` ⚠ | 0.05 | Sells in one short window only (**bad**) |
| `trademark_risk` ⚠ | 0.06 | The obvious title is somebody's mark (**bad**) |

A signal may be written as a bare number, but `demand_volume`, `proven_sales`
and `differentiation` must carry `evidence` or gate 1 fails the build. That is
the checklist item — *you can name the actual demand signal, not a hunch* —
made enforceable.

## Scouting many niches at once

`kdp scout niches.csv` scores a CSV and ranks it, so a week's shortlist gets
scored before any of it gets built:

```csv
niche,book_type,signal.demand_volume,evidence.demand_volume,signal.proven_sales
Large print word search for seniors,puzzle,8.5,"14800/mo (Helium 10)",8
Sudoku for absolute beginners,puzzle,5,"1300/mo and falling",4
```

Columns are `niche`, `book_type`, then `signal.<key>` and `evidence.<key>`
pairs. Ranking is only a shortlist: a scouted row still needs a full niche file
before it can be built, because the gate will ask for the evidence.
