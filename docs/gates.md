# The gates

> Between them sit checks that can fail a build and stop it. A check that only
> ever produces advice is decoration.

Each check reports `PASS` or `FAIL` with a one-line reason. A `FAIL` on a
blocking check raises `GateFailure`, the pipeline stops, and the run directory
gets a `STOPPED.md` explaining what to fix. Advisory checks (`WARN`) report
without stopping anything.

## G1 — niche gate

Runs after station 1, on `niche.json` and `niche_score.json`.

| Check | Fails when |
| --- | --- |
| `score_threshold` | Weighted score is under `niche_gate.min_score` |
| `signals_complete` | One of the ten signals was never scored |
| `demand_evidence_named` | A load-bearing signal has a score but no evidence — a hunch |
| `demand_floor` | `demand_volume` is under `min_demand_signal` |
| `trademark_checked` | `trademark.checked` is false, or names no source |
| `trademark_clear` | There are hits, or `trademark_risk` exceeds the maximum |
| `incumbent_gap_named` | No named weakness in the incumbent and no evidenced angle |
| `audience_named` | The niche has no audience |
| `keyword_seeds` | Fewer than three keyword seeds — the listing would be improvised |
| `trim_size_valid` / `paper_valid` / `target_pages_printable` | The constraints are not printable |

## G2 — substance gate

Runs after station 2. **Opens the interior PDF and reads the text.** This is
Prompt 3 in code: *you did not write it — read it as a buyer who paid for it.*

| Criterion | Check | Fails when |
| --- | --- | --- |
| Padding | `padding` | Filler pages exceed `quality.max_filler_page_ratio` |
| Repetition | `repetition_exact` | Distinct content units fall under `min_unique_content_ratio` |
| Repetition | `repetition_near_duplicate` | Two units overlap more than `max_pairwise_similarity` |
| Promise | `promise_numbers` | A number in the title or subtitle matches no real quantity |
| Promise | `promise_kept` *(advisory)* | The niche's promise is nowhere in the book's own words |
| Value | `value_content_share` | Content pages fall under `min_content_page_ratio` |
| Value | `value_no_empty_content_pages` | A page marked content is effectively blank |
| Value | `content_reaches_the_page` | A content unit is in the plan but not on the page it claims |
| Errors | `errors_placeholders` | `lorem ipsum`, `TODO`, `{{`, … reached the PDF |
| Errors | `errors_format_verification` | A format check failed (e.g. a puzzle word is not in its grid) |
| Completeness | `completeness_front_matter`, `completeness_back_matter` | Too few of either |
| Completeness | `completeness_title_page`, `completeness_copyright` | Page 1 has no title; no copyright page |
| Completeness | `completeness_page_numbers` | Numbered pages do not show their number in the PDF |
| — | `llm_second_opinion` *(advisory unless enabled to block)* | The optional model grader says no |

## G3 — print gate

Runs after station 3, on both PDFs.

| Check | Fails when |
| --- | --- |
| `page_count_matches_plan` | The PDF's page count differs from the plan's |
| `page_count_printable` | Outside KDP's minimum, maximum, or odd |
| `landed_on_target` | The page count missed the target — on purpose, or by accident? |
| `interior_trim_size` | A page is not exactly the trim size |
| `cover_is_one_page` | The wrap is not a single page |
| `cover_size_matches_page_count` | The wrap does not match the geometry this page count demands |
| `spine_recomputed_from_real_file` | The recorded spine differs from the one recomputed from the PDF |

## Watching your gates

> Track how often a gate actually fails something. Never failing is as bad a
> sign as always failing.

Every gate run appends a line to `output/_telemetry/gates.jsonl`. `kdp gates`
turns that into a fail rate per gate, per check, and says plainly when a gate
has stopped catching anything.

## Tuning them

Thresholds live in `QualityBar` and `NicheGateConfig` (`config.py`), set from
the `quality:` and `niche_gate:` blocks of your engine config. Raising a
threshold makes a gate stricter. Lowering one to get a green build is how a gate
stops being a gate.
