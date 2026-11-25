"""Embeddings locais e gratuitos (fastembed / ONNX, sem PyTorch).

O mesmo modelo gera os vetores dos filmes (offline) e da pergunta (no backend),
o que garante que os dois vivem no mesmo espaço vetorial.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from cinegraph.config import get_settings


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    s = get_settings()
    s.embed_cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=s.embed_model, cache_dir=str(s.embed_cache_dir))


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return (m / np.clip(norms, 1e-12, None)).astype(np.float32)


def embed_texts(texts: list[str], batch_size: int = 128) -> np.ndarray:
    vecs = np.stack(list(_model().embed(texts, batch_size=batch_size)))
    return _normalize(vecs)


def embed_query(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def dimension() -> int:
    return int(embed_query("dimensão").shape[0])
