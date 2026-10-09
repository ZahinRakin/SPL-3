"""
Shared vector helpers for the retrievers: embedding with a fallback, and cosine search.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend.core.logger import logger
from .llm_provider import embed, embed_many

EMBED_DIM = 768   # nomic-embed-text

# The random fallback keeps the live app usable when Ollama hiccups (D6), but in an
# evaluation it would quietly turn retrieval into noise. The evaluation CLIs turn on strict
# mode, so an embedding failure stops the run instead.
_strict_embeddings = False


def set_strict_embeddings(strict: bool) -> None:
    """strict=True: embedding failures raise instead of falling back to random vectors."""
    global _strict_embeddings
    _strict_embeddings = strict


# ── embedding with fallback ───────────────────────────────────────────────────

def fallback_embedding(text: str) -> List[float]:
    """Random unit vector used when the embedding service fails (decision D6).
    Note: hash() is salted per Python process, so this is not stable across runs."""
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    v = rng.standard_normal(EMBED_DIM).astype(float)
    return (v / np.linalg.norm(v)).tolist()


async def embed_or_fallback(text: str, owner: str, task_type: str = "retrieval_document") -> List[float]:
    try:
        return await embed(text, task_type=task_type)
    except Exception as exc:
        if _strict_embeddings:
            raise
        logger.error(f"{owner}: embedding failed, using RANDOM fallback (retrieval degraded): {exc}",
                     exc_info=True)
        return fallback_embedding(text)


async def embed_many_or_fallback(
    texts: List[str], owner: str, task_type: str = "retrieval_document"
) -> List[List[float]]:
    """Embed a batch in one Ollama round trip. If the batch fails, every text gets a fallback."""
    try:
        return await embed_many(texts, task_type=task_type)
    except Exception as exc:
        if _strict_embeddings:
            raise
        logger.error(
            f"{owner}: batch embedding of {len(texts)} texts failed, using RANDOM fallbacks "
            f"(retrieval degraded): {exc}",
            exc_info=True,
        )
        return [fallback_embedding(t) for t in texts]


# ── cosine search ─────────────────────────────────────────────────────────────

class EmbeddingIndex:
    """Node embeddings stacked into one matrix, so a query is scored against every
    node in a single NumPy operation instead of a Python loop."""

    def __init__(self, ids: List[str], vectors: List[List[float]]):
        self.ids = ids
        self.row = {nid: i for i, nid in enumerate(ids)}
        self.matrix = np.array(vectors, dtype=float) if ids else np.zeros((0, EMBED_DIM))
        self.norms = np.linalg.norm(self.matrix, axis=1)

    @classmethod
    def build(cls, embeddings: Dict[str, Optional[List[float]]]) -> "EmbeddingIndex":
        # Nodes without an embedding are skipped, as the original loops did.
        ids = [nid for nid, emb in embeddings.items() if emb]
        return cls(ids, [embeddings[nid] for nid in ids])

    def cosine(
        self, query: List[float], ids: Optional[Sequence[str]] = None
    ) -> Tuple[List[str], np.ndarray]:
        """Cosine similarity of `query` to every node (or only to `ids`, in that order).
        Returns the ids that were scored and their similarities; 0.0 where a norm is 0."""
        if ids is None:
            out_ids, rows = self.ids, slice(None)
        else:
            out_ids = [nid for nid in ids if nid in self.row]
            rows = [self.row[nid] for nid in out_ids]
        q = np.asarray(query, dtype=float)
        denom = self.norms[rows] * np.linalg.norm(q)
        dots = self.matrix[rows] @ q if out_ids else np.zeros(0)
        sims = np.divide(dots, denom, out=np.zeros(len(out_ids)), where=denom > 0)
        return out_ids, sims
