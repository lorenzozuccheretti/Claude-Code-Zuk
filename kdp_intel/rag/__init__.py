"""Retrieval-augmented generation: the store, and how evidence reaches a prompt."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..config import Credentials
from ..models import Hit
from .embeddings import get_embedder
from .store import ChromaStore, MemoryStore, PineconeStore, VectorStore


def open_store(workdir: Path, backend: str = "chroma", embedder: str = "hashing",
               creds: Credentials | None = None) -> VectorStore:
    emb = get_embedder(embedder)
    if backend == "memory":
        return MemoryStore(emb)
    if backend == "chroma":
        return ChromaStore(str(workdir / "chroma"), emb)
    if backend == "pinecone":
        creds = creds or Credentials.from_env()
        if not (creds.pinecone_key and creds.pinecone_index):
            raise RuntimeError("PINECONE_API_KEY and PINECONE_INDEX must both be set")
        return PineconeStore(creds.pinecone_key, creds.pinecone_index, emb, namespace=workdir.name)
    raise ValueError(f"unknown vector backend {backend!r}: memory | chroma | pinecone")


def retrieve(store: VectorStore, queries: list[str], k: int = 6, *, max_authority: int | None = None,
             max_age_days: int | None = None, today: date | None = None) -> list[Hit]:
    """Best hits across several queries, deduplicated by chunk."""
    floor = (today or date.today()) - timedelta(days=max_age_days) if max_age_days else None
    best: dict[str, Hit] = {}
    for q in queries:
        for hit in store.query(q, k, max_authority=max_authority, min_published=floor):
            if hit.chunk.id not in best or hit.score > best[hit.chunk.id].score:
                best[hit.chunk.id] = hit
    return sorted(best.values(), key=lambda h: (h.chunk.authority, -h.score))


def format_evidence(hits: list[Hit]) -> str:
    """Evidence as the writer and the fact-checker see it: every passage
    labelled with the id to cite, its tier and its date."""
    blocks = []
    for h in hits:
        c = h.chunk
        when = c.published or f"consultato {c.retrieved}"
        blocks.append(
            f'<evidence source_id="{c.source_id}" tier="{c.authority}" kind="{c.kind}" '
            f'publisher="{c.publisher}" date="{when}">\n{c.title}\n{c.text}\n</evidence>'
        )
    return "\n\n".join(blocks)


__all__ = ["open_store", "retrieve", "format_evidence", "VectorStore"]
