"""Perfil textual por filme e geração dos embeddings.

Cada filme vira um documento curto com as tags (as aplicadas por mais
usuários primeiro) e os gêneros em inglês e português. O título fica de fora
do texto vetorizado: nos testes (docs/decisoes.md) ele gerava casamentos
lexicais falsos ("Family", "Disturbing"). Filmes sem tag nem gênero usam só o
título. O MovieLens não traz sinopses; elas poderiam entrar depois como
enriquecimento via TMDb (links.csv).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np

from cinegraph.config import GENRES_PT, PROCESSED_DIR, get_settings
from cinegraph.retrieval.embedder import embed_texts

MAX_TAGS = 30  # o modelo trunca em 128 tokens; 30 tags cabem na maioria dos casos


def profile_text(display_title: str, year: int | None, genres: list[str], tags: list[str]) -> str:
    parts = []
    if tags:
        parts.append("Tags: " + ", ".join(tags[:MAX_TAGS]) + ".")
    if genres:
        parts.append("Genres: " + ", ".join(f"{g} ({GENRES_PT[g]})" for g in genres) + ".")
    if not parts:
        parts.append(f"Title: {display_title}" + (f" ({year})" if year else "") + ".")
    return " ".join(parts)


def build_profiles() -> list[dict]:
    s = get_settings()
    conn = sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True)
    tags: dict[int, list[str]] = {}
    for movie_id, tag in conn.execute(
        "SELECT movie_id, tag FROM movie_tags ORDER BY movie_id, n_users DESC, n_applications DESC, tag"
    ):
        tags.setdefault(movie_id, []).append(tag)
    profiles = []
    for movie_id, display_title, year, genres in conn.execute(
        "SELECT movie_id, display_title, year, genres FROM movies ORDER BY movie_id"
    ):
        genre_list = [g for g in genres.split("|") if g]
        text = profile_text(display_title, year, genre_list, tags.get(movie_id, []))
        profiles.append({"movie_id": movie_id, "text": text})
    conn.close()
    return profiles


def build_index() -> dict:
    from cinegraph.retrieval.vectorstore import POPULARITY_SHARE, augment_items

    s = get_settings()
    profiles = build_profiles()
    base = embed_texts([p["text"] for p in profiles])
    ids = np.array([p["movie_id"] for p in profiles], dtype=np.int64)
    conn = sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True)
    n_by_id = dict(conn.execute("SELECT movie_id, n_ratings FROM movies").fetchall())
    conn.close()
    n_ratings = np.array([n_by_id[int(i)] for i in ids], dtype=np.float64)
    n_max = int(n_ratings.max())
    vectors = augment_items(base, n_ratings, n_max)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    np.save(s.embeddings_path, vectors)
    np.save(s.embedding_ids_path, ids)
    with s.profiles_path.open("w") as f:
        for p in profiles:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embed_model": s.embed_model,
        "dimension": int(vectors.shape[1]),
        "content_dimension": int(base.shape[1]),
        "popularity_share": POPULARITY_SHARE,
        "n_ratings_max": n_max,
        "n_vectors": int(vectors.shape[0]),
        "embeddings_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
        "profiles_with_tags": sum(1 for p in profiles if "Tags:" in p["text"]),
    }
    (PROCESSED_DIR / "index_meta.json").write_text(json.dumps(meta, indent=2))
    return meta
