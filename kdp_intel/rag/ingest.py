"""Fetch documents, turn them into clean text, chunk them, store them.

Each source gets a short, stable, readable id (``istat-3fa2c1``): it is what
the writer cites and what a reader of the fact-check report sees. A
``sources.json`` manifest next to the vector store keeps the bibliography
data (title, publisher, URL, dates) independent of the vector backend.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..authority import classify, domain
from ..models import Chunk, SourceDoc, SourceSpec
from .store import VectorStore

_DROP = re.compile(r"<(script|style|nav|footer|header|aside|form|noscript|svg)\b.*?</\1>", re.S | re.I)
_BREAK = re.compile(r"</?(p|div|li|h[1-6]|tr|br|section|article|table)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_DATE_META = re.compile(
    r'<meta[^>]+(?:property|name|itemprop)="(?:article:published_time|article:modified_time|datePublished|'
    r'dateModified|dc\.date(?:\.issued)?|date)"[^>]*content="(\d{4}-\d{2}-\d{2})'
    r'|"date(?:Published|Modified)"\s*:\s*"(\d{4}-\d{2}-\d{2})',
    re.I,
)
_MONTHS = {m: i for i, m in enumerate(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split(), 1)}
# Official Italian pages state their date in prose: "Ultimo aggiornamento: 14 febbraio 2025".
_DATE_PROSE = re.compile(
    r"(?:Ultimo aggiornamento|Data (?:di )?pubblicazione|Pubblicato il|Aggiornato al)\s*:?\s*"
    r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})",
    re.I,
)
_SUFFIXES = {"it", "com", "eu", "org", "net", "gov", "info", "europa"}


def page_date(page: str) -> date | None:
    m = _DATE_META.search(page)
    if m:
        return date.fromisoformat(m.group(1) or m.group(2))
    m = _DATE_PROSE.search(_TAG.sub(" ", page))
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def source_id(url: str) -> str:
    labels = [p for p in domain(url).split(".") if p not in _SUFFIXES] or domain(url).split(".")
    label = labels[-1]
    return f"{re.sub(r'[^a-z0-9]', '', label)[:12] or 'src'}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def html_to_text(page: str) -> tuple[str, str, date | None]:
    """(title, text, published) from an HTML page."""
    title_m = _TITLE.search(page)
    title = html_lib.unescape(_TAG.sub("", title_m.group(1))).strip() if title_m else ""
    published = page_date(page)
    body = _BREAK.sub("\n", _DROP.sub(" ", page))
    text = html_lib.unescape(_TAG.sub(" ", body))
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    # Menus and cookie banners are short lines; keep lines that read as prose,
    # plus short ones that carry a number (table cells, amounts, dates).
    kept = [ln for ln in lines if len(ln) > 40 or (ln and re.search(r"\d", ln))]
    return title, "\n".join(kept), published


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def chunk_text(text: str, size: int = 1100, overlap: int = 200) -> list[str]:
    """Paragraph-aware chunks of roughly ``size`` characters."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks, current = [], ""
    for para in paras:
        while len(para) > size:  # a single huge paragraph: split on sentences
            cut = para.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if len(current) + len(para) + 1 > size and current:
            chunks.append(current)
            current = current[-overlap:].split(" ", 1)[-1] if overlap else ""
        current = f"{current}\n{para}".strip()
    if current:
        chunks.append(current)
    return chunks


def to_chunks(doc: SourceDoc) -> list[Chunk]:
    return [
        Chunk(
            id=f"{doc.id}:{i:04d}", source_id=doc.id, ordinal=i, text=text, url=doc.url,
            title=doc.title, publisher=doc.publisher, kind=doc.kind, authority=doc.authority,
            published=doc.published.isoformat() if doc.published else "",
            retrieved=doc.retrieved.isoformat(),
        )
        for i, text in enumerate(chunk_text(doc.text))
    ]


class Ingestor:
    def __init__(self, store: VectorStore, manifest_path: str | Path, fetcher: Any,
                 unblocker: Any = None, today: date | None = None) -> None:
        self.store = store
        self.manifest_path = Path(manifest_path)
        self.fetcher, self.unblocker = fetcher, unblocker
        self.today = today or date.today()
        self.manifest: dict[str, dict[str, Any]] = (
            json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest_path.exists() else {}
        )
        self.errors: list[str] = []

    def _save(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, doc: SourceDoc) -> int:
        chunks = to_chunks(doc)
        if not chunks:
            self.errors.append(f"{doc.url}: no usable text")
            return 0
        self.store.upsert(chunks)
        self.manifest[doc.id] = doc.model_dump(mode="json", exclude={"text"}) | {"chunks": len(chunks)}
        self._save()
        return len(chunks)

    def _get(self, url: str) -> tuple[bytes, str]:
        try:
            return self.fetcher.fetch_bytes(url)
        except Exception as first:  # noqa: BLE001 - try the unblocker before giving up
            if self.unblocker is None:
                raise
            try:
                return self.unblocker.fetch_bytes(url)
            except Exception:  # noqa: BLE001
                raise first from None

    def ingest_url(self, spec: SourceSpec) -> int:
        try:
            body, ctype = self._get(spec.url)
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            self.errors.append(f"{spec.url}: {exc}")
            return 0
        if "pdf" in ctype or spec.url.lower().endswith(".pdf") or body[:5] == b"%PDF-":
            title, text, published = spec.title, pdf_to_text(body), None
        else:
            title, text, published = html_to_text(body.decode("utf-8", errors="replace"))
        kind, tier = classify(spec.url)
        if spec.kind:
            kind = spec.kind
            tier = {"law": 1, "official": 1, "press": 2}.get(kind, 3)
        if kind == "law" and not spec.published:
            published = None  # a law in force does not go stale; its page date means nothing
        doc = SourceDoc(
            id=source_id(spec.url), url=spec.url, title=spec.title or title or spec.url,
            publisher=spec.publisher or domain(spec.url), kind=kind, authority=tier,
            published=spec.published or published, retrieved=self.today, text=text,
        )
        return self.add(doc)

    def ingest_thread(self, thread: dict[str, Any]) -> int:
        """A forum thread is evidence of how readers talk, never of facts."""
        text = "\n\n".join([thread.get("title", ""), thread.get("text", ""), *thread.get("comments", [])])
        doc = SourceDoc(
            id=source_id(thread["url"]), url=thread["url"], title=thread.get("title", ""),
            publisher=domain(thread["url"]), kind="community", authority=3,
            published=date.fromisoformat(thread["created"]) if thread.get("created") else None,
            retrieved=self.today, text=text,
        )
        return self.add(doc)

    def ingest_all(self, specs: list[SourceSpec]) -> dict[str, int]:
        return {spec.url: self.ingest_url(spec) for spec in specs}
