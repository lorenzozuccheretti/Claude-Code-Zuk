"""Typst typesetter: block model -> ``.typ`` source -> print-ready PDF.

Text from the writer is never pasted into Typst markup. Every run of plain
text becomes a string literal (``#"..."``), so characters that mean
something to Typst (``#``, ``*``, ``_``, ``//`` in a URL, a leading ``-``)
can never change the layout or break the build.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..models import Block
from .common import FONTS_DIR, PART_LABELS, BookContent, Geometry, citation_text, inline_tokens

TEMPLATE = Path(__file__).with_name("templates") / "kdp.typ"


def lit(text: str) -> str:
    """A Typst string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def caption(title: str) -> str:
    """The figure adds "Tabella N"; drop one the writer already typed."""
    return re.sub(r"^\s*Tabella\s+\d+[.:]?\s*", "", title)


class TypstWriter:
    def __init__(self, book: BookContent) -> None:
        self.book = book
        self.chapter_no = 0
        self.cited_in_chapter: set[str] = set()
        self.cited_anywhere: list[str] = []
        self.indexed_in_chapter: set[str] = set()

    # ------------------------------------------------------------ inline

    def _cite(self, sid: str) -> str:
        label = f"fn-c{self.chapter_no}-{sid}"
        if sid not in self.cited_anywhere:
            self.cited_anywhere.append(sid)
        if sid in self.cited_in_chapter:
            return f"#footnote(<{label}>)"
        self.cited_in_chapter.add(sid)
        text, url = citation_text(self.book.sources.get(sid), sid)
        link = f" #link({lit(url)})[#{lit(url)}]" if url else ""
        return f"#footnote[#{lit(text + '.')}{link}]<{label}>"  # no space: it would print

    def _index_marks(self, plain: str) -> str:
        marks = ""
        low = plain.lower()
        for term in self.book.index_terms:
            if term not in self.indexed_in_chapter and re.search(rf"\b{re.escape(term.lower())}\b", low):
                self.indexed_in_chapter.add(term)
                marks += f"#idx({lit(term)})"
        return marks

    def inline(self, text: str) -> str:
        out = []
        for kind, value in inline_tokens(text):
            if kind == "text":
                out.append(f"#{lit(value)}{self._index_marks(value)}")
            elif kind == "strong":
                out.append(f"#strong({lit(value)}){self._index_marks(value)}")
            elif kind == "emph":
                out.append(f"#emph({lit(value)})")
            else:
                out.append(self._cite(value))
        return "".join(out)

    # ------------------------------------------------------------ blocks

    def block(self, b: Block) -> str:
        if b.type == "heading":
            return f"=== {self.inline(b.text)}\n"
        if b.type == "paragraph":
            return f"{self.inline(b.text)}\n"
        if b.type == "bullets":
            return "".join(f"- {self.inline(i)}\n" for i in b.items)
        if b.type == "numbered":
            return "".join(f"+ {self.inline(i)}\n" for i in b.items)
        if b.type == "checklist":
            items = ", ".join(f"[{self.inline(i)}]" for i in b.items)
            return f"#checklist({items})\n"
        if b.type == "callout":
            kind = b.kind if b.kind != "none" else "consiglio"
            return f"#callout({lit(kind)}, {lit(b.title)})[{self.inline(b.text)}]\n"
        if b.type == "table":
            header = ", ".join(f"[{self.inline(h)}]" for h in b.header)
            rows = ", ".join(f"[{self.inline(c)}]" for r in b.rows for c in r)
            return f"#data-table(caption: {lit(caption(b.title))}, header: ({header},), {rows})\n"
        raise ValueError(f"unknown block type {b.type}")

    # ------------------------------------------------------------ book

    def front_matter(self) -> str:
        m, o = self.book.meta, self.book.outline
        isbn = f"ISBN {m.isbn}" if m.isbn else "ISBN assegnato da Amazon KDP"
        return f"""
#page(header: none, footer: none)[
  #v(28%)
  #align(center)[
    #text(font: display-font, size: 24pt, weight: "semibold", hyphenate: false)[#{lit(m.title)}]
    #v(10pt)
    #text(size: 12pt, style: "italic")[#{lit(m.subtitle or o.subtitle)}]
    #v(1fr)
    #text(font: display-font, size: 11pt, tracking: 1pt)[#{lit(m.author.upper())}]
    #v(6pt)
    #text(font: display-font, size: 8pt, tracking: 1.5pt)[#{lit(m.publisher.upper())}]
  ]
]
#page(header: none, footer: none)[
  #set text(size: 7.5pt)
  #set par(justify: false, spacing: 0.9em)
  #v(1fr)
  #{lit(m.title)} \\
  #{lit(m.subtitle or o.subtitle)}

  © {m.year} #{lit(m.author)}. Tutti i diritti riservati. \\
  #{lit(m.edition)}, {m.year}. #{lit(m.publisher)}. \\
  #{lit(isbn)}

  Nessuna parte di questa pubblicazione può essere riprodotta, archiviata o trasmessa in qualsiasi forma o con qualsiasi mezzo senza l'autorizzazione scritta dell'autore, salvo brevi citazioni a fini di recensione.

  Dati, norme e importi aggiornati al #{lit(self.book.facts_as_of)}. Le fonti di ogni dato sono indicate in nota e nell'elenco «Fonti e riferimenti».

  #{lit(m.disclaimer)}
]
#page(header: none, footer: none)[
  #text(font: display-font, size: 16pt, weight: "semibold")[Indice]
  #v(12pt)
  #set text(size: 9.5pt)
  #show outline.entry.where(level: 1): it => {{ v(8pt); strong(it) }}
  #outline(title: none, depth: 2, indent: 0em)
]
#end-chapter()
#main-matter()
"""

    def chapters(self) -> str:
        out = []
        outline = self.book.outline
        part_index = {p: i for i, p in enumerate(outline.parts)}
        seen_parts: set[str] = set()
        for spec, draft in zip(outline.chapters, self.book.chapters, strict=True):
            pi = part_index[spec.part]
            if 0 < pi < len(outline.parts) - 1 and spec.part not in seen_parts:
                seen_parts.add(spec.part)
                out.append(f"#part({lit(PART_LABELS[pi])}, {lit(spec.part)})\n")
            self.chapter_no = spec.number
            self.cited_in_chapter, self.indexed_in_chapter = set(), set()
            numbered = 0 < pi < len(outline.parts) - 1
            out.append(f"#chapter({lit(draft.title or spec.title)}, numbered: {str(numbered).lower()})\n")
            out += [self.block(b) for b in draft.blocks]
            out.append("#end-chapter()\n")
        return "\n".join(out)

    def back_matter(self) -> str:
        groups = {1: "Fonti normative e istituzionali", 2: "Stampa professionale e di settore",
                  3: "Comunità e testimonianze"}
        lines = ['#chapter("Fonti e riferimenti", numbered: false)',
                 "#set text(size: 8.5pt)", "#set par(justify: false, spacing: 0.7em)"]
        for tier, label in groups.items():
            sids = [s for s in self.cited_anywhere if self.book.sources.get(s, {}).get("authority") == tier]
            if not sids:
                continue
            lines.append(f"=== {lit(label)[1:-1]}")
            for sid in sids:
                text, url = citation_text(self.book.sources.get(sid), sid)
                link = f" #link({lit(url)})[#{lit(url)}]" if url else ""
                lines.append(f"- #{lit(text + '.')}{link}")
        lines.append("#end-chapter()")
        if self.book.index_terms:
            lines += ['#chapter("Indice analitico", numbered: false)', "#analytic-index()", "#end-chapter()"]
        return "\n".join(lines) + "\n"

    def document(self, geo: Geometry) -> str:
        m = self.book.meta
        head = (
            '#import "kdp.typ": *\n'
            f"#show: book.with(title: {lit(m.title)}, subtitle: {lit(m.subtitle)}, author: {lit(m.author)}, "
            f"trim-w: {geo.trim_w}in, trim-h: {geo.trim_h}in, inside: {geo.inside}in, "
            f"outside: {geo.outside}in, top: {geo.top}in, bottom: {geo.bottom}in)\n"
        )
        # Chapters before back matter: the bibliography lists what was cited.
        body = self.chapters()
        return head + self.front_matter() + body + self.back_matter()


def compile_typst(source: str, build_dir: Path, name: str = "interior") -> Path:
    import typst  # noqa: PLC0415

    build_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, build_dir / TEMPLATE.name)
    main = build_dir / f"{name}.typ"
    main.write_text(source, encoding="utf-8")
    pdf = build_dir / f"{name}.pdf"
    typst.compile(str(main), output=str(pdf), root=str(build_dir),
                  font_paths=[str(FONTS_DIR)], ignore_system_fonts=True)
    return pdf
