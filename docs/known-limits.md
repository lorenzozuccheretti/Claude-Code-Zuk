# What breaks first when you run this fifty times

Prompt 2 asks the useful question: *what part of this is weakest?* An honest
answer, from the inside.

## 1. The template packs are the recurring cost

This is the first thing you will hit, and it shows up in two ways.

**Within one book**, the generator refuses to draw two prompts that read alike,
so the effective pool is smaller than the line count suggests. Ask for a book
longer than the pack can fill and it raises rather than padding:

```
ConfigError: only 84 of 109 prompts could be drawn without two of them
overlapping more than 70% …
```

**Across books**, a seed knows nothing about what you already published. Two
120-page journals built from the same niche with different seeds share 27-38% of
their prompts (measured over six builds; a 380-line pool, 109 drawn). Pass
`--avoid` to fix it:

```bash
kdp build niche.yaml --seed 1 --output output/
kdp build niche.yaml --seed 2 --output output/ --avoid output/   # 0% overlap
```

With `--avoid`, book two is disjoint from book one — until the pack runs dry,
at which point the engine refuses to publish a third rather than repeating
itself. So **the packs are the part of this engine that needs feeding**: budget
content writing, not code, as the recurring cost. The same applies to
`wordlists.yaml` (41 themes; a theme reused inside one book needs 16+ words) and
`planner.yaml` (56 focus lines caps a planner at 56 weeks).

## 2. KDP's DOM will move

`upload/selectors.yaml` is a guess about someone else's HTML, and Amazon does
not version it. Expect the driver to report `MISS` on fields after a redesign.
It reports rather than guesses, and the fix is one YAML edit — but it is
maintenance you do not control. The dry run and the listing file are unaffected,
which is why they are the real deliverable.

## 3. Print specs drift

Printing costs and trim tables change. Everything lives in `spec/kdp_spec.yaml`
with a `last_verified` date, but nothing in the engine can tell you the file is
stale. Check it against KDP's documentation before a launch, and move the date.

## 4. The substance gate cannot read

It checks structure, quantities, duplication and presence — things code can
settle. It cannot tell you that a prompt is boring, that a title is
embarrassing, or that the cover looks like a 2011 PowerPoint. It will pass a
book that is technically flawless and commercially dead. That is what station 6
is for, and why the engine stops before Publish.

The LLM hook narrows this gap a little, and is advisory by default on purpose: a
model's opinion is not a build-stopping criterion unless you decide it is.

## 5. Covers are typographic, not illustrated

The cover renderer produces a clean, typographic wrap with correct geometry. It
does not place artwork, and in categories where covers sell the book, that is a
real limitation. The geometry, safe areas and barcode keep-out are exactly right,
so the sane workflow is: let the engine compute the wrap and hand `cover_spec.json`
to a designer.

## 6. Scale limits nobody has hit yet

- The near-duplicate check is O(n²) over content units. At ~800 units it is
  still under a second; a 3,000-unit book would be noticeable.
- Puzzle placement retries at most 400 times per word. Long words in a small
  grid raise `PuzzleGenerationError` rather than dropping a word — correct, but
  you will meet it if you push `words_per_puzzle` up.
- Nothing parallelises. Fifty books is fifty sequential builds, a few seconds
  each.

## 7. What this does not fix

A factory multiplies output. It does nothing to the number of people who want
what you make. Point it at a dead niche and you will produce dead books very
efficiently — faster than before, which is worse, not better.
