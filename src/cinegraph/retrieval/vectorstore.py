"""Banco vetorial: duas implementações com a mesma interface.

- `LocalVectorStore`: matriz numpy com busca exata. Usada nos testes e na
  avaliação, para não gastar a cota diária de leituras do Firestore.
- `FirestoreVectorStore`: busca vetorial nativa do Firestore (índice `flat`,
  produto interno). Usada pelo site em produção.

Prior de popularidade embutido no vetor
---------------------------------------
Muitos filmes sem tag têm perfis idênticos (só gêneros) e, portanto, a mesma
similaridade. Se a popularidade fosse aplicada depois da busca, cada backend
devolveria um subconjunto arbitrário desses empates. Por isso o vetor de cada
filme ganha uma dimensão extra:

    v' = [ v·√(1−s) , √s·p ]        q' = [ q·√(1−s) , √s ]
    q'·v' = (1−s)·cos(q, v) + s·p,   p = ln(1+n_avaliações) / ln(1+n_max)

com s = POPULARITY_SHARE. O próprio banco já ordena por "similaridade +
popularidade", e os dois backends devolvem a mesma ordem.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Protocol

import numpy as np

from cinegraph.config import PROCESSED_DIR, get_settings

POPULARITY_SHARE = 0.10


@lru_cache(maxsize=1)
def _n_max() -> int:
    meta_path = PROCESSED_DIR / "index_meta.json"
    return int(json.loads(meta_path.read_text())["n_ratings_max"])


def popularity(n_ratings: int) -> float:
    return math.log1p(max(0, n_ratings)) / math.log1p(_n_max())


def augment_query(q: np.ndarray) -> np.ndarray:
    return np.concatenate([q * math.sqrt(1 - POPULARITY_SHARE), [math.sqrt(POPULARITY_SHARE)]]).astype(np.float32)


def augment_items(base: np.ndarray, n_ratings: np.ndarray, n_max: int) -> np.ndarray:
    p = np.log1p(n_ratings) / math.log1p(n_max)
    return np.hstack([base * math.sqrt(1 - POPULARITY_SHARE), (math.sqrt(POPULARITY_SHARE) * p)[:, None]]).astype(np.float32)


def base_part(v: np.ndarray) -> np.ndarray:
    return (v[:-1] / math.sqrt(1 - POPULARITY_SHARE)).astype(np.float32)


def combined_score(cosine: float, n_ratings: int) -> float:
    return (1 - POPULARITY_SHARE) * cosine + POPULARITY_SHARE * popularity(n_ratings)


def cosine_from_score(score: float, n_ratings: int) -> float:
    return (score - POPULARITY_SHARE * popularity(n_ratings)) / (1 - POPULARITY_SHARE)


class VectorStore(Protocol):
    name: str

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        """Recebe o vetor da consulta (384 dim.) e devolve [(movie_id, score)] em ordem decrescente."""
        ...

    def get_vector(self, movie_id: int) -> np.ndarray | None:
        """Vetor de conteúdo do filme (384 dim., sem o componente de popularidade)."""
        ...


class LocalVectorStore:
    name = "local"

    def __init__(self) -> None:
        s = get_settings()
        self.matrix = np.load(s.embeddings_path)
        self.ids = np.load(s.embedding_ids_path)
        self.pos = {int(m): i for i, m in enumerate(self.ids)}

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        scores = self.matrix @ augment_query(vector)
        k = min(k, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.lexsort((self.ids[top], -scores[top]))]
        return [(int(self.ids[i]), float(scores[i])) for i in top]

    def get_vector(self, movie_id: int) -> np.ndarray | None:
        i = self.pos.get(int(movie_id))
        return None if i is None else base_part(self.matrix[i])


class FirestoreVectorStore:
    name = "firestore"
    MAX_LIMIT = 1000  # limite do Firestore para find_nearest

    def __init__(self, project: str | None = None) -> None:
        from google.cloud import firestore

        s = get_settings()
        self.client = firestore.Client(project=project or s.gcp_project)
        self.collection = self.client.collection(s.firestore_collection)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        query = self.collection.select(["movie_id", "vector_distance"]).find_nearest(
            vector_field="embedding",
            query_vector=Vector(augment_query(vector).astype(float).tolist()),
            distance_measure=DistanceMeasure.DOT_PRODUCT,
            limit=min(k, self.MAX_LIMIT),
            distance_result_field="vector_distance",
        )
        out = [(int(d.to_dict()["movie_id"]), float(d.to_dict()["vector_distance"])) for d in query.stream()]
        out.sort(key=lambda x: (-x[1], x[0]))
        return out

    def get_vector(self, movie_id: int) -> np.ndarray | None:
        snap = self.collection.document(str(int(movie_id))).get(["embedding"])
        if not snap.exists:
            return None
        emb = snap.to_dict().get("embedding")
        return None if emb is None else base_part(np.asarray(list(emb), dtype=np.float32))


@lru_cache(maxsize=2)
def get_vector_store(backend: str | None = None) -> VectorStore:
    backend = backend or get_settings().vector_backend
    if backend == "firestore":
        return FirestoreVectorStore()
    if backend == "local":
        return LocalVectorStore()
    raise ValueError(f"Backend vetorial desconhecido: {backend}")


def upload_to_firestore(project: str | None = None, force: bool = False) -> dict:
    """Envia os vetores para o Firestore (1 escrita por filme, ~9,7 mil no total).

    Um documento `_meta/index` guarda o SHA-256 dos embeddings: se for igual ao
    local, nada é reenviado e a cota diária de escritas fica preservada.
    """
    import sqlite3

    from google.cloud import firestore
    from google.cloud.firestore_v1.vector import Vector

    s = get_settings()
    meta = json.loads((PROCESSED_DIR / "index_meta.json").read_text())
    client = firestore.Client(project=project or s.gcp_project)
    meta_ref = client.collection("_meta").document("index")
    remote = meta_ref.get()
    if remote.exists and remote.to_dict().get("embeddings_sha256") == meta["embeddings_sha256"] and not force:
        return {"status": "já atualizado", **meta}

    matrix = np.load(s.embeddings_path)
    ids = np.load(s.embedding_ids_path)
    conn = sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True)
    info = {r[0]: r[1:] for r in conn.execute("SELECT movie_id, display_title, year, genres, n_ratings FROM movies")}
    coll = client.collection(s.firestore_collection)
    writer = client.bulk_writer()
    for movie_id, vec in zip(ids.tolist(), matrix):
        title, year, genres, n_ratings = info[movie_id]
        writer.set(
            coll.document(str(movie_id)),
            {
                "movie_id": movie_id,
                "display_title": title,
                "year": year,
                "genres": [g for g in genres.split("|") if g],
                "n_ratings": n_ratings,
                "embedding": Vector(vec.astype(float).tolist()),
            },
        )
    writer.close()
    meta_ref.set(meta)
    return {"status": "enviado", "documents": int(len(ids)), **meta}


def compare_backends(queries: list[str], k: int = 10) -> list[dict]:
    """Confere se Firestore e numpy devolvem o mesmo top-k (gasta ~100 leituras por consulta)."""
    from cinegraph.retrieval.embedder import embed_query

    local, remote = get_vector_store("local"), get_vector_store("firestore")
    out = []
    for q in queries:
        v = embed_query(q)
        a, b = local.search(v, k), remote.search(v, k)
        out.append({
            "query": q,
            "same_order": [m for m, _ in a] == [m for m, _ in b],
            "overlap": len({m for m, _ in a} & {m for m, _ in b}) / k,
            "same_scores": bool(np.allclose([s for _, s in a], [s for _, s in b], atol=1e-5)),
        })
    return out
