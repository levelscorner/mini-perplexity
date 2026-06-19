"""FAISS-backed vector index for S7 Memory.

Why FAISS? It's small, in-process, no server. `IndexFlatIP` does inner
product, which on L2-normalised vectors equals cosine similarity. We
normalise on add() and search() so cosine is what the agent gets.

FAISS only knows integer positions in insertion order. The application
keeps a parallel list of string ids so it can map a search result's
position back to its MemoryItem id.

The dimension is "married" on first add() — change the embedding model
later and every vector in the persisted index is garbage. The gateway
pins the model (nomic 768-dim) so this stays true across runs.

Persists to two files under state/:
    state/index.faiss      the binary FAISS index
    state/index_ids.json   the parallel ids list
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

try:
    import faiss  # type: ignore[import-untyped]
except ImportError as e:
    raise SystemExit("faiss-cpu is required for S7. Run: uv add faiss-cpu") from e


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalise a 1D vector. After normalisation IP == cosine."""
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


class VectorIndex:
    """An in-memory FAISS index with disk persistence.

    Dimension is decided on first add(). Subsequent adds with a different
    dim raise — the marriage rule, enforced.
    """

    def __init__(self, store_dir: Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.store_dir / "index.faiss"
        self.ids_path = self.store_dir / "index_ids.json"
        self._index: faiss.IndexFlatIP | None = None
        self._ids: list[str] = []
        self._dim: int | None = None
        self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self) -> None:
        if self.index_path.exists() and self.ids_path.exists():
            self._index = faiss.read_index(str(self.index_path))
            self._ids = json.loads(self.ids_path.read_text())
            self._dim = self._index.d

    def persist(self) -> None:
        if self._index is None:
            return
        faiss.write_index(self._index, str(self.index_path))
        self.ids_path.write_text(json.dumps(self._ids))

    def clear(self) -> None:
        self._index = None
        self._ids = []
        self._dim = None
        for p in (self.index_path, self.ids_path):
            if p.exists():
                p.unlink()

    # ── mutation ───────────────────────────────────────────────────────
    def add(self, item_id: str, embedding: list[float]) -> None:
        vec = _l2_normalize(np.array(embedding, dtype=np.float32))
        if self._index is None:
            self._dim = vec.shape[0]
            self._index = faiss.IndexFlatIP(self._dim)
        elif vec.shape[0] != self._dim:
            raise ValueError(
                f"Embedding dim {vec.shape[0]} doesn't match index dim {self._dim}. "
                "The embedding model must stay fixed for the lifetime of the index."
            )
        self._index.add(vec.reshape(1, -1))
        self._ids.append(item_id)

    # ── query ──────────────────────────────────────────────────────────
    def search(self, query_embedding: list[float], k: int = 5) -> list[tuple[str, float]]:
        """Return up to k (item_id, cosine_similarity) pairs ranked by similarity."""
        if self._index is None or self._index.ntotal == 0:
            return []
        vec = _l2_normalize(np.array(query_embedding, dtype=np.float32))
        scores, idxs = self._index.search(vec.reshape(1, -1), min(k, self._index.ntotal))
        out: list[tuple[str, float]] = []
        for score, idx in zip(scores[0].tolist(), idxs[0].tolist()):
            if idx < 0:
                continue
            out.append((self._ids[idx], float(score)))
        return out

    @property
    def size(self) -> int:
        return self._index.ntotal if self._index is not None else 0

    @property
    def dim(self) -> int | None:
        return self._dim
