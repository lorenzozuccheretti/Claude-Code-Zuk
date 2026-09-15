# Adding a book type

A new format is a module and a decorator. The engine does not need to learn what
a word search is — it only handles `InteriorPlan` objects.

## 1. Write the module

```python
# kdp_factory/booktypes/colouring.py
from .base import BookType, ContentUnit, InteriorPlan, PageSpec, TitleProposal, register


@register
class ColouringBookType(BookType):
    key = "colouring"
    label = "Colouring book"
    description = "One line-art page per spread, blank on the reverse."
    default_options = {"target_pages": 120, "images_per_book": None}

    def plan(self, niche, config, rng, options=None) -> InteriorPlan:
        opts = {**self.options_for(niche), **(options or {})}
        pages = self._front_matter(...)           # title, copyright, belongs-to, how-to
        units = []
        for i, motif in enumerate(motifs):
            pages.append(PageSpec("colouring_page", kind="content", data={...}))
            units.append(ContentUnit("motif", motif, page_index=len(pages) - 1, probe=motif))
        pages.extend(self._back_matter(...))
        pages = self._pad_to_even(pages)
        return InteriorPlan(self.key, title, subtitle, trim, paper, pages, units, metadata)

    def titles(self, niche, rng) -> TitleProposal: ...

    def verify(self, plan):
        """What code can settle about this format. Gate 2 enforces the result."""
        return [("reverse_pages_blank", ..., "every image has a blank reverse")]
```

Import it in `booktypes/__init__.py` so registration happens.

## 2. Add the templates it draws from

Renderers dispatch on `PageSpec.template`, so any new template name needs a
method in `render/interior.py`:

```python
self.templates["colouring_page"] = self._colouring_page
```

Content lives in `content/templates/*.yaml`, not in Python. Keep it there:
adding vocabulary should be an edit, not a deploy.

## 3. Make the page count land on purpose

Every shipped type solves for its page count rather than padding to it:

```python
def _solve_layout(self, target_pages, pinned, ...):
    # front + content + extras + back == target, exactly
```

Gate 3 compares `landed_on_target` against the PDF, so a type that drifts will
say so on every run.

## 4. Respect the bar you will be graded against

Two rules keep a new type honest:

- **Draw diverse content.** Use `content.packs.select_diverse` with
  `config.quality.max_pairwise_similarity`, so the generator holds itself to the
  same near-duplicate bar the substance gate applies.
- **Refuse rather than pad.** If the template pack cannot fill the book, raise
  `ConfigError` naming what to add. Shipping repeats to hit a page count is the
  exact failure the gate exists to catch.

## 5. Categories and keywords

Add an entry to `content/templates/categories.yaml` under `book_types` (three
defaults, plus tag-promoted alternatives) and a `keyword_modifiers` block.
Station 4 fails loudly if a book type has no categories rather than shipping a
listing with empty slots.

## 6. Test it

`tests/test_booktypes.py` parametrises over every registered type, so a new one
inherits the shared contract tests: page count lands on target, page count is
even, content units are distinct, front and back matter exist, its own
verification passes, and planning is deterministic.
