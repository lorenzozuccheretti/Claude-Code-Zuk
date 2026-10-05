---
name: kdp-autopilot
description: Run the KDP-Intelligence-Engine autopilot unattended - pick a book topic from what Italians type on Amazon.it, validate it (keyword, competition, official sources), write, fact-check and typeset the book - answering the engine's hand-off requests yourself with your own web search. Use when the user asks to "run the autopilot", "make the next book automatically", "scegli il tema e crea il libro", or schedules it as a routine.
---

# KDP autopilot (hand-off mode)

The engine (`kdpi autopilot`) does every measurement in code: Amazon.it book-search
autocomplete, the IBS catalogue, fetching and classifying sources, fact-check gates,
typesetting. It asks a model only for judgement, and in hand-off mode **you are the
model**: each question is a file `<task>.request.md`; you write `<task>.json` next to it
and run the command again. Nothing costs money: no API key is needed.

## Loop

1. `kdpi autopilot intel_projects/autopilot.yaml --llm handoff` (add `--today YYYY-MM-DD`
   only when the user pins a date). Exit code 3 = waiting; the output lists
   `→ rispondi a: <path>`.
2. Answer **every** listed request (rules below), writing valid JSON for the schema at the
   end of the request. Then run step 1 again.
3. Stop when the status is `printed` or `proof` (book done), `no_topic` (no topic passed the
   gates - report the reasons from the log, never lower the gates yourself), or `blocked`
   (chapters that could not be proven - report which and why).
4. Report: chosen topic and why (the gate lines in the log), PDF path, `07_listing.md`,
   the cover guide in `06_cover/`, and anything the log flags as missing.

Answers are cached by content: a rerun replays them for free. Never edit or delete an
answer to get past a gate.

## How to answer each request (by file prefix)

- **`webresults-*`** - run your WebSearch tool with the query. For `site:dominio`, use
  `allowed_domains` with that domain and drop the `site:` part from the query. Copy only
  pages the tool returned: exact URL, title, a snippet, `published` as ISO date if shown
  (else ""). Never invent or "fix" a URL; an empty `results` list is a valid answer.
- **`nicheideas-*`** - group the listed phrases into book topics as the system text says.
  `keywords` must be copied exactly from the list (code drops anything else).
- **`personadraft-*`**, **`outline-*`**, **`reviewthemesdraft-*`** - follow the system text;
  use only the evidence in the request; write "non emerso" where it says so.
- **`listingdraft-*`** - title or subtitle contain the primary keyword exactly; 7 backend
  keywords ≤ 50 characters that do not repeat title words; no "bestseller", "gratis",
  brand names; description 600-4000 characters, from the real chapters.
- **`chapterdraft-*`** - write the chapter in Italian from the evidence only. Every sentence
  with a number, amount, percentage, date, deadline or law reference ends with a citation
  marker `[[S:id]]` of a source in the evidence, and its figures must appear in that
  source. In `caso_pratico` callouts, scenario figures need no citation, but each computed
  figure must follow in one step (sum, difference, product, share, percentage) from figures
  already written or from the cited rule. If the request contains **"Revisione."**, fix
  exactly the listed sentences and keep the rest.
- **`entailmentbatch-*`** - for each claim decide from the evidence shown under it only:
  `supported` / `contradicted` need a `quote` copied **verbatim** from that evidence
  (the code rejects any quote that is not a substring); otherwise `not_enough_info` with an
  empty quote. Claims marked `esempio="si"`: judge only the rule they apply.

## Honesty rules

- Never answer a request with content the evidence does not support, and never mark a claim
  supported to unblock a chapter: a blocked book is a correct outcome.
- Do not change the profile's gates, sources or the engine to make a topic pass without
  telling the user.
- Author and publisher come from the profile. If they are empty the book stays a proof;
  ask the user for them instead of inventing a name.
