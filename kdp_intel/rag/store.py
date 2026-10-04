"""Vector stores: in-memory (tests), ChromaDB (default, local) and Pinecone.

All three take the same filters, so an agent never knows which one it has:

* ``max_authority`` - only sources at this tier or better (1 is best)
* ``source_ids``    - only these sources (the fact-checker reading a citation)
* ``min_published`` - drop press and community documents published before
  this date; tier 1 (laws, official pages) and undated documents always
  pass, because an official page stays valid until it is replaced
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from ..models import Chunk, Hit
from .embeddings import Embedder
from .lexical import BM25Stats, fuse

_EPOCH = date(1970, 1, 1)


def _ordinal(iso: str) -> int:
    return (date.fromisoformat(iso) - _EPOCH).days if iso else 0


class VectorStore(Protocol):
    embedder: Embedder

    def upsert(self, chunks: list[Chunk]) -> None: ...

    def query(
        self, text: str, k: int = 8, *, max_authority: int | None = None,
        source_ids: list[str] | None = None, min_published: date | None = None,
    ) -> list[Hit]: ...

    def count(self) -> int: ...


def _meta(chunk: Chunk) -> dict[str, Any]:
    meta = chunk.metadata()
    meta["published_ord"] = _ordinal(chunk.published)
    return meta


def hybrid(query: str, pool: list[Hit], stats: BM25Stats, k: int) -> list[Hit]:
    """Re-rank a wide vector pool by reciprocal-rank fusion with BM25."""
    for h in pool:
        h.lexical = round(stats.score(query, h.chunk.text), 4)
    by_vec = [h.chunk.id for h in sorted(pool, key=lambda h: (-h.score, h.chunk.id))]
    by_lex = [h.chunk.id for h in sorted(pool, key=lambda h: (-h.lexical, h.chunk.id)) if h.lexical > 0]
    fused = fuse(by_vec, by_lex)
    return sorted(pool, key=lambda h: (-fused[h.chunk.id], h.chunk.id))[:k]


def _pool(k: int) -> int:
    return max(40, k * 6)


def _chunk(cid: str, text: str, meta: dict[str, Any]) -> Chunk:
    fields = {k: v for k, v in meta.items() if k in Chunk.model_fields}
    return Chunk(id=cid, text=text, **fields)


class MemoryStore:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._rows: dict[str, tuple[Chunk, list[float]]] = {}
        self.stats = BM25Stats()

    def upsert(self, chunks: list[Chunk]) -> None:
        for chunk, vec in zip(chunks, self.embedder.embed([c.text for c in chunks])):
            self._rows[chunk.id] = (chunk, vec)
            self.stats.add(chunk.id, chunk.text)

    def query(self, text, k=8, *, max_authority=None, source_ids=None, min_published=None):
        q = self.embedder.embed([text])[0]
        floor = (min_published - _EPOCH).days if min_published else None
        hits = []
        for chunk, vec in self._rows.values():
            if max_authority is not None and chunk.authority > max_authority:
                continue
            if source_ids is not None and chunk.source_id not in source_ids:
                continue
            if (floor is not None and chunk.authority > 1 and chunk.published
                    and _ordinal(chunk.published) < floor):
                continue
            hits.append(Hit(chunk=chunk, score=sum(a * b for a, b in zip(q, vec))))
        hits.sort(key=lambda h: (-h.score, h.chunk.id))
        return hybrid(text, hits[:_pool(k)], self.stats, k)

    def count(self) -> int:
        return len(self._rows)


def _where(max_authority, source_ids, min_published) -> dict[str, Any] | None:
    clauses: list[dict[str, Any]] = []
    if max_authority is not None:
        clauses.append({"authority": {"$lte": max_authority}})
    if source_ids is not None:
        clauses.append({"source_id": {"$in": list(source_ids)}})
    if min_published is not None:
        floor = (min_published - _EPOCH).days
        clauses.append({"$or": [{"authority": {"$eq": 1}}, {"published_ord": {"$eq": 0}},
                                {"published_ord": {"$gte": floor}}]})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaStore:
    """Persistent local store. Embeddings are always computed by us and passed
    in, so Chroma never downloads its own embedding model."""

    def __init__(self, path: str, embedder: Embedder, collection: str = "kdp_intel") -> None:
        import chromadb  # noqa: PLC0415

        self.embedder = embedder
        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(
            name=f"{collection}-{embedder.name.replace('/', '_')}"[:63],
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        self._stats: BM25Stats | None = None
        self._stats_count = -1

    def _bm25(self) -> BM25Stats:
        if self._stats is None or self._stats_count != self.count():
            stats = BM25Stats()
            data = self._col.get(include=["documents"])
            for cid, doc in zip(data["ids"], data["documents"]):
                stats.add(cid, doc)
            self._stats, self._stats_count = stats, self.count()
        return self._stats

    def upsert(self, chunks: list[Chunk]) -> None:
        for i in range(0, len(chunks), 256):
            batch = chunks[i:i + 256]
            self._col.upsert(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[_meta(c) for c in batch],
                embeddings=self.embedder.embed([c.text for c in batch]),
            )

    def query(self, text, k=8, *, max_authority=None, source_ids=None, min_published=None):
        if self.count() == 0:
            return []
        res = self._col.query(
            query_embeddings=self.embedder.embed([text]),
            n_results=min(_pool(k), self.count()),
            where=_where(max_authority, source_ids, min_published),
            include=["documents", "metadatas", "distances"],
        )
        pool = [
            Hit(chunk=_chunk(cid, doc, meta), score=1.0 - dist)
            for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                            res["metadatas"][0], res["distances"][0])
        ]
        return hybrid(text, pool, self._bm25(), k)

    def count(self) -> int:
        return self._col.count()


class PineconeStore:
    """Pinecone serverless index (dimension must equal the embedder's).

    Chunk text travels in metadata, so a query returns evidence, not just ids.
    """

    def __init__(self, api_key: str, index: str, embedder: Embedder, namespace: str = "") -> None:
        from pinecone import Pinecone  # noqa: PLC0415

        self.embedder = embedder
        self.namespace = namespace
        self._index = Pinecone(api_key=api_key).Index(index)

    def upsert(self, chunks: list[Chunk]) -> None:
        for i in range(0, len(chunks), 100):
            batch = chunks[i:i + 100]
            vectors = self.embedder.embed([c.text for c in batch])
            self._index.upsert(
                vectors=[{"id": c.id, "values": v, "metadata": {**_meta(c), "text": c.text}}
                         for c, v in zip(batch, vectors)],
                namespace=self.namespace,
            )

    def query(self, text, k=8, *, max_authority=None, source_ids=None, min_published=None):
        res = self._index.query(
            vector=self.embedder.embed([text])[0], top_k=k, include_metadata=True,
            filter=_where(max_authority, source_ids, min_published), namespace=self.namespace,
        )
        hits = []
        for m in res.get("matches", []):
            meta = dict(m["metadata"])
            hits.append(Hit(chunk=_chunk(m["id"], meta.pop("text", ""), meta), score=float(m["score"])))
        return hits

    def count(self) -> int:
        stats = self._index.describe_index_stats()
        ns = stats.get("namespaces", {}).get(self.namespace, {})
        return int(ns.get("vector_count", 0))
