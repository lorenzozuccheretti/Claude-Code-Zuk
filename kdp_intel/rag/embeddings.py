"""Text to vectors.

``HashingEmbedder`` needs no model download and no network: it hashes word
and character n-grams into a fixed-size vector. It is lexical, which suits
legal Italian well (article numbers, "D.Lgs. 139/2024", amounts) and makes
tests deterministic. For semantic recall on paraphrased questions, install
``sentence-transformers`` and use the multilingual model.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# Function words carry no topic; dropping them lets "decessi" outweigh "il".
STOPWORDS = frozenset("""
a ad al alla alle allo ai agli all anche avere c che chi ci col come con cui da dal dalla dalle dai
dagli dall degli dei del della delle dello di dove e ed era essere gli ha hanno i il in io la le lo
loro ma mi ne nei nel nella nelle nello noi non o per perche piu po quale quali quando quanto questa
queste questi questo se si sia sono su sua sue sui sul sulla suo tra un una uno vi gia cosi poi ogni
""".split())


class HashingEmbedder:
    name = "hashing-v2"

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = [w for w in re.findall(r"[a-z0-9]+(?:[./][a-z0-9]+)*", _normalise(text))
                 if w not in STOPWORDS]
        feats = list(words)
        feats += [f"{a}_{b}" for a, b in zip(words, words[1:])]
        for w in words:
            padded = f"#{w}#"
            feats += [padded[i:i + 4] for i in range(max(1, len(padded) - 3))]
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            counts: dict[str, int] = {}
            for feat in self._features(text):
                counts[feat] = counts.get(feat, 0) + 1
            vec = [0.0] * self.dim
            for feat, tf in counts.items():  # sublinear tf: repetition is not relevance
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
                weight = (1.0 + math.log(tf)) * (2.0 if "_" in feat else 1.0)
                vec[h % self.dim] += weight if (h >> 63) == 0 else -weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SentenceTransformerEmbedder:
    def __init__(self, model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model)
        self.name = model
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.encode(texts, normalize_embeddings=True)]


def get_embedder(name: str = "hashing") -> Embedder:
    if name == "hashing":
        return HashingEmbedder()
    if name in ("multilingual", "sentence-transformers"):
        return SentenceTransformerEmbedder()
    return SentenceTransformerEmbedder(name)
