// KDP interior template for Italian non-fiction.
//
// Geometry comes from Python (computed from the KDP spec card for the real
// page count); this file only decides how things look.
//
// Structure:  part = heading level 1 (own recto page, no running header)
//             chapter = heading level 2 (always opens on a recto)
//             section = heading level 3
// Blank versos inserted to make a chapter open on the right carry no header
// and no folio, as in a professionally set book.

#let body-font = "Lora"
#let display-font = "Karla"
#let ink = luma(15%)
#let rule-ink = luma(55%)
#let tint = luma(93%)

#let callout-labels = (
  attenzione: "ATTENZIONE",
  caso_pratico: "CASO PRATICO",
  consiglio: "CONSIGLIO DELL'ESPERTO",
  dato_chiave: "DATO CHIAVE",
)

// A page is blank if it lies after the end of one chapter and before the
// opening of the next.
#let is-blank(p) = {
  let ends = query(<chapter-end>).map(e => e.location().page())
  let opens = (
    query(heading.where(level: 2)).map(h => h.location().page())
      + query(heading.where(level: 1)).map(h => h.location().page())
  )
  ends.any(e => e < p and opens.filter(o => o > e).sorted().at(0, default: 1000000) > p)
}

#let opens-here(p) = (
  query(heading.where(level: 1)).any(h => h.location().page() == p)
    or query(heading.where(level: 2)).any(h => h.location().page() == p)
)

#let in-main(p) = query(<main-matter>).any(m => m.location().page() <= p)

#let book(
  title: "", subtitle: "", author: "",
  trim-w: 6in, trim-h: 9in,
  inside: 0.75in, outside: 0.5in, top: 0.75in, bottom: 0.8in,
  body-size: 10.5pt,
  doc,
) = {
  set document(title: title, author: author)
  set text(font: body-font, size: body-size, lang: "it", region: "IT", hyphenate: true, fill: ink)
  set par(justify: true, leading: 0.72em, spacing: 1.05em, linebreaks: "optimized")
  set page(
    width: trim-w, height: trim-h,
    margin: (inside: inside, outside: outside, top: top, bottom: bottom),
    header-ascent: 35%,
    footer-descent: 35%,
    header: context {
      let p = here().page()
      if not in-main(p) or opens-here(p) or is-blank(p) { return }
      set text(font: display-font, size: 7.5pt, tracking: 0.6pt)
      let chapters = query(selector(heading.where(level: 2)).before(here()))
      let current = if chapters.len() > 0 { upper(chapters.last().body) } else { "" }
      if calc.even(p) [#upper(title) #h(1fr)] else [#h(1fr) #current]
      v(-4pt)
      line(length: 100%, stroke: 0.4pt + rule-ink)
    },
    footer: context {
      let p = here().page()
      if not in-main(p) or is-blank(p) { return }
      if query(heading.where(level: 1)).any(h => h.location().page() == p) { return }
      set text(font: display-font, size: 8pt)
      align(center, counter(page).display())
    },
  )

  // Headings only style their text; part() and chapter() below do the
  // page breaks, so the heading sits on the page it opens.
  show heading.where(level: 1): it => block(text(font: display-font, size: 22pt, weight: "semibold", it.body))
  show heading.where(level: 2): it => block(
    text(font: display-font, size: 20pt, weight: "semibold", hyphenate: false, it.body))

  show heading.where(level: 3): it => block(above: 1.6em, below: 0.8em, sticky: true,
    text(font: display-font, size: 11.5pt, weight: "semibold", hyphenate: false, it.body))

  show strong: set text(weight: "semibold")
  set footnote.entry(separator: line(length: 25%, stroke: 0.4pt + rule-ink), gap: 0.4em)
  show footnote.entry: set text(size: 7.5pt)
  show link: set text(fill: ink)
  show figure.caption: set text(font: display-font, size: 8.5pt)
  show figure: set block(breakable: true)
  set list(indent: 0.6em, body-indent: 0.5em, marker: ([•], [–]))
  set enum(indent: 0.6em, body-indent: 0.5em)
  set table(stroke: none)
  show table: set text(size: 8.5pt)
  show table: set par(justify: false, leading: 0.55em)
  doc
}

#let chapter-counter = counter("chapter")

#let end-chapter() = [#metadata("end") <chapter-end>]

#let part(label, title) = {
  pagebreak(to: "odd", weak: true)
  v(30%)
  set align(center)
  text(font: display-font, size: 9pt, tracking: 2pt, upper(label))
  v(6pt)
  heading(level: 1, title)
  v(10pt)
  line(length: 25%, stroke: 0.6pt + rule-ink)
  end-chapter()
}

// Introduction and conclusion are chapters without a number.
#let chapter(title, numbered: true) = {
  pagebreak(to: "odd", weak: true)
  counter(footnote).update(0)
  if numbered { chapter-counter.step() }
  v(16%)
  text(font: display-font, size: 8.5pt, tracking: 2pt, fill: rule-ink,
    if numbered [CAPITOLO #context chapter-counter.display()] else [#sym.space.nobreak])
  v(2pt)
  heading(level: 2, title)
  v(4pt)
  line(length: 18%, stroke: 1.2pt + ink)
  v(20pt)
}

#let callout(kind, title, body) = {
  let label = callout-labels.at(kind, default: upper(kind))
  block(
    width: 100%, breakable: true, above: 1.3em, below: 1.3em,
    fill: tint, inset: (left: 12pt, right: 10pt, top: 9pt, bottom: 9pt),
    stroke: (left: 2.5pt + ink),
    {
      set par(justify: false)
      text(font: display-font, size: 7.5pt, weight: "semibold", tracking: 1.4pt, label)
      if title != "" {
        linebreak()
        text(font: display-font, size: 10pt, weight: "semibold", title)
      }
      v(2pt)
      set text(size: 9.5pt)
      body
    },
  )
}

#let checkbox = box(width: 0.75em, height: 0.75em, stroke: 0.6pt + ink, baseline: 0.08em)

#let checklist(..items) = block(above: 1em, below: 1em, {
  for it in items.pos() {
    grid(columns: (1.4em, 1fr), row-gutter: 0.55em, checkbox, it)
    v(0.35em, weak: true)
  }
})

#let data-table(caption: "", header: (), ..rows) = {
  let n = header.len()
  figure(
    kind: table, supplement: [Tabella],
    caption: if caption != "" { figure.caption(position: top, caption) },
    table(
      columns: range(n).map(_ => 1fr),
      align: left + top,
      inset: (x: 5pt, y: 4.5pt),
      fill: (_, y) => if y == 0 { luma(82%) } else if calc.even(y) { tint } else { none },
      stroke: (_, y) => if y == 0 { (bottom: 0.8pt + ink) } else { (bottom: 0.3pt + rule-ink) },
      table.header(..header.map(h => text(font: display-font, weight: "semibold", h))),
      ..rows.pos().flatten(),
    ),
  )
}

#let idx(term) = [#metadata(term) <idx>]

#let analytic-index() = context {
  let entries = (:)
  for m in query(<idx>) {
    let page = counter(page).at(m.location()).first()
    let key = m.value
    let pages = entries.at(key, default: ())
    if page not in pages { pages.push(page) }
    entries.insert(key, pages)
  }
  set par(justify: false, spacing: 0.45em)
  set text(size: 9pt)
  columns(2, gutter: 14pt, {
    let letter = ""
    for key in entries.keys().sorted(key: k => lower(k)) {
      let first = upper(key.first())
      if first != letter {
        letter = first
        v(0.8em)
        text(font: display-font, weight: "semibold", letter)
        parbreak()
      }
      [#key #h(0.4em) #entries.at(key).map(str).join(", ")]
      parbreak()
    }
  })
}

#let main-matter() = {
  pagebreak(to: "odd", weak: true)
  counter(page).update(1)
  [#metadata("main") <main-matter>]
}
