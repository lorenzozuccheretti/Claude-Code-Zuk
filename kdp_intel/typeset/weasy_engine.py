"""WeasyPrint typesetter: block model -> HTML + CSS Paged Media -> PDF.

The same book as the Typst engine, from the same blocks: running heads from
``string-set``, recto chapter openings from ``break-before: right``, blank
versos styled by ``@page :blank``, footnotes from ``float: footnote``, and
page numbers in the contents and the index from ``target-counter``.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..models import Block
from .common import FONTS_DIR, PART_LABELS, BookContent, Geometry, citation_text, inline_tokens

FONTS = [
    ("Lora", "Lora-Regular.ttf", 400, "normal"),
    ("Lora", "Lora-Italic.ttf", 400, "italic"),
    ("Lora", "Lora-SemiBold.ttf", 600, "normal"),
    ("Karla", "Karla-Regular.ttf", 400, "normal"),
    ("Karla", "Karla-SemiBold.ttf", 600, "normal"),
]

CALLOUT_LABELS = {
    "attenzione": "Attenzione", "caso_pratico": "Caso pratico",
    "consiglio": "Consiglio dell'esperto", "dato_chiave": "Dato chiave",
}


def css(geo: Geometry, title: str) -> str:
    faces = "\n".join(
        f'@font-face {{ font-family: "{fam}"; src: url("{(FONTS_DIR / f).as_uri()}"); '
        f"font-weight: {w}; font-style: {st}; }}"
        for fam, f, w, st in FONTS
    )
    head = 'font-family: Karla; font-size: 7.5pt; letter-spacing: 0.6pt; text-transform: uppercase;'
    return f"""{faces}
@page {{ size: {geo.trim_w}in {geo.trim_h}in; margin-top: {geo.top}in; margin-bottom: {geo.bottom}in;
  @bottom-center {{ content: counter(page); font-family: Karla; font-size: 8pt; }} }}
@page :left {{ margin-left: {geo.outside}in; margin-right: {geo.inside}in;
  @top-left {{ content: "{html.escape(title)}"; {head} border-bottom: 0.4pt solid #888; width: 100%; }} }}
@page :right {{ margin-left: {geo.inside}in; margin-right: {geo.outside}in;
  @top-right {{ content: string(chapter); {head} border-bottom: 0.4pt solid #888; width: 100%; }} }}
@page :blank {{ @top-left {{ content: none; }} @top-right {{ content: none; }} @bottom-center {{ content: none; }} }}
@page front {{ @top-left {{ content: none; }} @top-right {{ content: none; }} @bottom-center {{ content: none; }} }}
@page part {{ @top-left {{ content: none; }} @top-right {{ content: none; }} @bottom-center {{ content: none; }} }}
@page :nth(1 of opening) {{ counter-reset: page 1; @top-left {{ content: none; }} @top-right {{ content: none; }} }}
@page :nth(1 of chapter) {{ @top-left {{ content: none; }} @top-right {{ content: none; }} }}
html {{ font-family: Lora; font-size: 10.5pt; line-height: 1.32; color: #262626; hyphens: auto; }}
body {{ margin: 0; }}
p {{ margin: 0 0 0.55em; text-align: justify; orphans: 2; widows: 2; }}
.front {{ page: front; break-after: page; }}
.title-page {{ text-align: center; padding-top: 28%; height: 6.5in; }}
.title-page h1 {{ font-family: Karla; font-weight: 600; font-size: 24pt; margin: 0 0 10pt; }}
.copyright {{ font-size: 7.5pt; padding-top: 3.8in; }}
.copyright p {{ text-align: left; }}
.toc h1 {{ font-family: Karla; font-size: 16pt; }}
.toc a {{ color: inherit; text-decoration: none; }}
.toc li {{ list-style: none; font-size: 9.5pt; margin: 2pt 0; }}
.toc li.part {{ font-weight: 600; margin-top: 8pt; }}
.toc a::after {{ content: leader('.') target-counter(attr(href), page); }}
.part-page {{ page: part; break-before: right; text-align: center; padding-top: 2.6in; }}
.part-page .label {{ font-family: Karla; font-size: 9pt; letter-spacing: 2pt; text-transform: uppercase; }}
.part-page h1 {{ font-family: Karla; font-weight: 600; font-size: 22pt; }}
section.chapter.opening {{ page: opening; }}
section.chapter {{ page: chapter; break-before: right; counter-reset: footnote; }}
.chapter .label {{ font-family: Karla; font-size: 8.5pt; letter-spacing: 2pt; color: #777; padding-top: 1.3in; }}
h2 {{ string-set: chapter content(text); font-family: Karla; font-weight: 600; font-size: 20pt;
  margin: 2pt 0 4pt; hyphens: manual; }}
h2::after {{ content: ""; display: block; width: 18%; border-bottom: 1.2pt solid #262626; margin: 6pt 0 20pt; }}
h3 {{ font-family: Karla; font-weight: 600; font-size: 11.5pt; margin: 1.4em 0 0.6em; break-after: avoid; }}
.fn {{ float: footnote; font-size: 7.5pt; line-height: 1.25; text-align: left; }}
::footnote-call {{ content: counter(footnote); vertical-align: super; font-size: 65%; line-height: 0; }}
::footnote-marker {{ content: counter(footnote) ". "; }}
@page {{ @footnote {{ border-top: 0.4pt solid #888; padding-top: 3pt; }} }}
.callout {{ background: #ededed; border-left: 2.5pt solid #262626; padding: 9pt 10pt 9pt 12pt; margin: 1.2em 0; }}
.callout .k {{ font-family: Karla; font-weight: 600; font-size: 7.5pt; letter-spacing: 1.4pt; text-transform: uppercase; }}
.callout .t {{ font-family: Karla; font-weight: 600; font-size: 10pt; display: block; }}
.callout p {{ font-size: 9.5pt; text-align: left; margin: 2pt 0 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; margin: 1em 0; line-height: 1.2; }}
caption {{ font-family: Karla; font-size: 8.5pt; caption-side: top; margin-bottom: 4pt; }}
th {{ font-family: Karla; font-weight: 600; text-align: left; background: #d1d1d1; border-bottom: 0.8pt solid #262626; padding: 4pt 5pt; }}
td {{ border-bottom: 0.3pt solid #888; padding: 4pt 5pt; vertical-align: top; }}
tr:nth-child(even) td {{ background: #ededed; }}
thead {{ display: table-header-group; }}
ul.check {{ list-style: none; padding-left: 0.2em; }}
ul.check li::before {{ content: ""; display: inline-block; width: 0.7em; height: 0.7em;
  border: 0.6pt solid #262626; margin-right: 0.6em; vertical-align: -0.05em; }}
ul, ol {{ padding-left: 1.4em; margin: 0.4em 0 0.8em; }}
.biblio li, .index p {{ font-size: 8.5pt; text-align: left; }}
.index {{ columns: 2; column-gap: 14pt; }}
.index a {{ color: inherit; text-decoration: none; }}
.index a::after {{ content: target-counter(attr(href), page); }}
a {{ color: inherit; text-decoration: none; overflow-wrap: anywhere; }}
"""


class HtmlWriter:
    def __init__(self, book: BookContent) -> None:
        self.book = book
        self.cited: list[str] = []
        self.index_hits: dict[str, list[str]] = {t: [] for t in book.index_terms}
        self._seen_terms: set[str] = set()
        self._noted: dict[str, str] = {}
        self._anchor = 0

    def _marks(self, plain: str) -> str:
        out = ""
        for term in self.book.index_terms:
            if term not in self._seen_terms and re.search(rf"\b{re.escape(term.lower())}\b", plain.lower()):
                self._seen_terms.add(term)
                self._anchor += 1
                anchor = f"ix{self._anchor}"
                self.index_hits[term].append(anchor)
                out += f'<span id="{anchor}"></span>'
        return out

    def inline(self, text: str) -> str:
        out = []
        for kind, value in inline_tokens(text):
            esc = html.escape(value)
            if kind == "text":
                out.append(esc + self._marks(value))
            elif kind == "strong":
                out.append(f"<strong>{esc}</strong>{self._marks(value)}")
            elif kind == "emph":
                out.append(f"<em>{esc}</em>")
            else:
                if value not in self.cited:
                    self.cited.append(value)
                if value in self._noted:  # one footnote per source per chapter
                    continue
                self._anchor += 1
                self._noted[value] = f"fn{self._anchor}"
                label, url = citation_text(self.book.sources.get(value), value)
                out.append(f'<span class="fn" id="fn{self._anchor}">{html.escape(label)}. {html.escape(url)}</span>')
        return "".join(out)

    def block(self, b: Block) -> str:
        if b.type == "heading":
            return f"<h3>{self.inline(b.text)}</h3>"
        if b.type == "paragraph":
            return f"<p>{self.inline(b.text)}</p>"
        if b.type in ("bullets", "numbered", "checklist"):
            tag = "ol" if b.type == "numbered" else "ul"
            cls = ' class="check"' if b.type == "checklist" else ""
            return f"<{tag}{cls}>" + "".join(f"<li>{self.inline(i)}</li>" for i in b.items) + f"</{tag}>"
        if b.type == "callout":
            kind = b.kind if b.kind != "none" else "consiglio"
            title = f'<span class="t">{html.escape(b.title)}</span>' if b.title else ""
            return (f'<div class="callout"><span class="k">{CALLOUT_LABELS[kind]}</span>{title}'
                    f"<p>{self.inline(b.text)}</p></div>")
        if b.type == "table":
            cap = f"<caption>{html.escape(b.title)}</caption>" if b.title else ""
            head = "".join(f"<th>{self.inline(h)}</th>" for h in b.header)
            body = "".join("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in r) + "</tr>" for r in b.rows)
            return f"<table>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        raise ValueError(f"unknown block type {b.type}")

    def document(self, geo: Geometry) -> str:
        m, o = self.book.meta, self.book.outline
        e = html.escape
        part_index = {p: i for i, p in enumerate(o.parts)}
        body, toc, seen = [], [], set()
        for n, (spec, draft) in enumerate(zip(o.chapters, self.book.chapters, strict=True)):
            pi = part_index[spec.part]
            middle = 0 < pi < len(o.parts) - 1
            if middle and spec.part not in seen:
                seen.add(spec.part)
                body.append(f'<div class="part-page" id="part{pi}"><div class="label">{PART_LABELS[pi]}</div>'
                            f"<h1>{e(spec.part)}</h1></div>")
                toc.append(f'<li class="part"><a href="#part{pi}">{e(spec.part)}</a></li>')
            self._seen_terms, self._noted = set(), {}
            number = sum(1 for s in o.chapters[: n + 1] if 0 < part_index[s.part] < len(o.parts) - 1)
            label = f"CAPITOLO {number}" if middle else "&nbsp;"
            title = draft.title or spec.title
            opening = " opening" if n == 0 else ""
            body.append(f'<section class="chapter{opening}" id="ch{spec.number}"><div class="label">{label}</div>'
                        f"<h2>{e(title)}</h2>" + "".join(self.block(b) for b in draft.blocks) + "</section>")
            toc.append(f'<li><a href="#ch{spec.number}">{e(title)}</a></li>')

        groups = {1: "Fonti normative e istituzionali", 2: "Stampa professionale e di settore",
                  3: "Comunità e testimonianze"}
        biblio = ['<section class="chapter" id="fonti"><div class="label">&nbsp;</div><h2>Fonti e riferimenti</h2>']
        for tier, label in groups.items():
            sids = [s for s in self.cited if self.book.sources.get(s, {}).get("authority") == tier]
            if sids:
                biblio.append(f"<h3>{label}</h3><ul class='biblio'>")
                for sid in sids:
                    text, url = citation_text(self.book.sources.get(sid), sid)
                    biblio.append(f"<li>{e(text)}. {e(url)}</li>")
                biblio.append("</ul>")
        biblio.append("</section>")
        toc.append('<li><a href="#fonti">Fonti e riferimenti</a></li>')

        index = ""
        if self.book.index_terms:
            rows = []
            for term in sorted(self.index_hits, key=str.lower):
                links = ", ".join(f'<a href="#{a}"></a>' for a in self.index_hits[term])
                if links:
                    rows.append(f"<p>{e(term)} {links}</p>")
            index = ('<section class="chapter" id="indice"><div class="label">&nbsp;</div><h2>Indice analitico</h2>'
                     f'<div class="index">{"".join(rows)}</div></section>')
            toc.append('<li><a href="#indice">Indice analitico</a></li>')

        isbn = f"ISBN {m.isbn}" if m.isbn else "ISBN assegnato da Amazon KDP"
        front = f"""
<div class="front title-page"><h1>{e(m.title)}</h1><p style="text-align:center"><em>{e(m.subtitle or o.subtitle)}</em></p>
<p style="text-align:center;margin-top:2.6in;font-family:Karla;letter-spacing:1pt">{e(m.author.upper())}</p>
<p style="text-align:center;font-family:Karla;font-size:8pt;letter-spacing:1.5pt">{e(m.publisher.upper())}</p></div>
<div class="front copyright"><p>{e(m.title)}<br>{e(m.subtitle or o.subtitle)}</p>
<p>© {m.year} {e(m.author)}. Tutti i diritti riservati.<br>{e(m.edition)}, {m.year}. {e(m.publisher)}.<br>{e(isbn)}</p>
<p>Nessuna parte di questa pubblicazione può essere riprodotta, archiviata o trasmessa in qualsiasi forma o con qualsiasi mezzo senza l'autorizzazione scritta dell'autore, salvo brevi citazioni a fini di recensione.</p>
<p>Dati, norme e importi aggiornati al {e(self.book.facts_as_of)}. Le fonti di ogni dato sono indicate in nota e nell'elenco «Fonti e riferimenti».</p>
<p>{e(m.disclaimer)}</p></div>
<div class="front toc"><h1>Indice</h1><ul>{"".join(toc)}</ul></div>
"""
        return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>{e(m.title)}</title>'
                f"<style>{css(geo, m.title)}</style></head><body>{front}{''.join(body)}{''.join(biblio)}{index}"
                "</body></html>")


def compile_html(source: str, build_dir: Path, name: str = "interior") -> Path:
    from weasyprint import HTML  # noqa: PLC0415

    build_dir.mkdir(parents=True, exist_ok=True)
    page = build_dir / f"{name}.html"
    page.write_text(source, encoding="utf-8")
    pdf = build_dir / f"{name}.pdf"
    HTML(filename=str(page)).write_pdf(str(pdf))
    return pdf
