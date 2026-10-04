"""BM25 over the whole archive, fused with vector similarity.

Vectors find paraphrases; BM25 finds the rare word that matters ("decessi",
"voltura", "139/2024"). Each store asks the vector index for a wide pool of
candidates, scores them with BM25 using document frequencies from the whole
archive, and merges the two rankings with reciprocal rank fusion, so neither
score scale has to be calibrated against the other.
"""

from __future__ import annotations

import math
import re
import unicodedata

from .embeddings import STOPWORDS

K1, B = 1.4, 0.75
RRF_K = 60


def stem(word: str) -> str:
    """Light Italian stemmer: enough to join decesso/decessi, erede/eredi."""
    for suffix in ("zioni", "zione", "mente", "ità", "ita", "ichi", "iche", "ici", "ico", "ica"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word[:-1] if len(word) > 4 and word[-1] in "aeio" else word


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [stem(w) for w in re.findall(r"[a-z0-9]+(?:[./][a-z0-9]+)*", text) if w not in STOPWORDS]


class BM25Stats:
    def __init__(self) -> None:
        self.df: dict[str, int] = {}
        self.docs = 0
        self.total_len = 0
        self._seen: set[str] = set()

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self._seen:
            return
        self._seen.add(doc_id)
        toks = tokens(text)
        self.docs += 1
        self.total_len += len(toks)
        for t in set(toks):
            self.df[t] = self.df.get(t, 0) + 1

    def score(self, query: str, text: str) -> float:
        if not self.docs:
            return 0.0
        doc = tokens(text)
        avg = self.total_len / self.docs
        tf: dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in set(tokens(query)):
            f = tf.get(q, 0)
            if not f:
                continue
            idf = math.log(1 + (self.docs - self.df.get(q, 0) + 0.5) / (self.df.get(q, 0) + 0.5))
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * len(doc) / avg))
        return s


def fuse(ids_by_vector: list[str], ids_by_bm25: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in (ids_by_vector, ids_by_bm25):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores
