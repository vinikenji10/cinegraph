"""Acesso somente leitura ao SQLite.

A conexão é aberta em modo `ro`, com `PRAGMA query_only` e um autorizador
que só permite leitura. Além disso, toda consulta gerada pelo modelo passa
pela validação de `cinegraph.sqltool` antes de chegar aqui.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import threading
import time
import unicodedata
from functools import lru_cache

from cinegraph.config import get_settings
from cinegraph.schemas import Filters

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_local = threading.local()


def _authorizer(action, arg1, arg2, dbname, source):  # noqa: ANN001
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def connect() -> sqlite3.Connection:
    """Uma conexão por thread (o FastAPI atende requisições em várias threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        path = get_settings().db_path
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_authorizer)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def run_query(sql: str, params: tuple | list = (), timeout_s: float = 3.0, max_rows: int = 200):
    """Executa uma consulta com limite de tempo. Retorna (colunas, linhas, truncado)."""
    conn = connect()
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description or []]
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(rows) > max_rows
    return columns, [tuple(r) for r in rows[:max_rows]], truncated


def fetch_movies(movie_ids: list[int], n_tags: int = 12) -> dict[int, dict]:
    """Registros completos dos filmes, na forma usada pela resposta e pela interface."""
    ids = [int(i) for i in dict.fromkeys(movie_ids)]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    conn = connect()
    out: dict[int, dict] = {}
    for r in conn.execute(
        f"SELECT movie_id, display_title, title, year, genres, n_ratings, avg_rating, bayes_rating, "
        f"imdb_id, tmdb_id FROM movies WHERE movie_id IN ({marks})",
        ids,
    ):
        out[r["movie_id"]] = {
            "movie_id": r["movie_id"],
            "title": r["display_title"],
            "original_title": r["title"],
            "year": r["year"],
            "genres": [g for g in r["genres"].split("|") if g],
            "n_ratings": r["n_ratings"],
            "avg_rating": r["avg_rating"],
            "bayes_rating": r["bayes_rating"],
            "imdb_id": r["imdb_id"],
            "tmdb_id": r["tmdb_id"],
            "tags": [],
        }
    for r in conn.execute(
        f"SELECT movie_id, tag FROM movie_tags WHERE movie_id IN ({marks}) "
        f"ORDER BY movie_id, n_users DESC, n_applications DESC, tag",
        ids,
    ):
        tags = out[r["movie_id"]]["tags"]
        if len(tags) < n_tags:
            tags.append(r["tag"])
    return out


def filter_ids(movie_ids: list[int], f: Filters) -> set[int]:
    """Aplica os filtros exatos (consulta parametrizada) sobre um conjunto de candidatos."""
    ids = [int(i) for i in dict.fromkeys(movie_ids)]
    if not ids:
        return set()
    where, params = [f"m.movie_id IN ({','.join('?' * len(ids))})"], list(ids)
    if f.year_min is not None:
        where.append("m.year >= ?"); params.append(f.year_min)
    if f.year_max is not None:
        where.append("m.year <= ?"); params.append(f.year_max)
    if f.min_avg_rating is not None:
        where.append("m.avg_rating >= ?"); params.append(f.min_avg_rating)
    if f.min_ratings is not None:
        where.append("m.n_ratings >= ?"); params.append(f.min_ratings)
    if f.genres_all:
        g = list(dict.fromkeys(f.genres_all))
        where.append(
            f"(SELECT COUNT(*) FROM movie_genres g WHERE g.movie_id = m.movie_id "
            f"AND g.genre IN ({','.join('?' * len(g))})) = ?"
        )
        params += g + [len(g)]
    if f.genres_any:
        where.append(
            f"EXISTS (SELECT 1 FROM movie_genres g WHERE g.movie_id = m.movie_id "
            f"AND g.genre IN ({','.join('?' * len(f.genres_any))}))"
        )
        params += list(f.genres_any)
    if f.genres_exclude:
        where.append(
            f"NOT EXISTS (SELECT 1 FROM movie_genres g WHERE g.movie_id = m.movie_id "
            f"AND g.genre IN ({','.join('?' * len(f.genres_exclude))}))"
        )
        params += list(f.genres_exclude)
    _, rows, _ = run_query(f"SELECT m.movie_id FROM movies m WHERE {' AND '.join(where)}", params, max_rows=10_000)
    return {r[0] for r in rows}


def satisfies(record: dict, f: Filters) -> bool:
    """Versão em Python de `filter_ids`, usada na avaliação de restrições."""
    g = set(record["genres"])
    year, avg, n = record.get("year"), record.get("avg_rating"), record.get("n_ratings", 0)
    checks = [
        f.year_min is None or (year is not None and year >= f.year_min),
        f.year_max is None or (year is not None and year <= f.year_max),
        f.min_avg_rating is None or (avg is not None and avg >= f.min_avg_rating),
        f.min_ratings is None or n >= f.min_ratings,
        set(f.genres_all) <= g,
        not f.genres_any or bool(g & set(f.genres_any)),
        not (g & set(f.genres_exclude)),
    ]
    return all(checks)


# ---------------------------------------------------------------- títulos

# Artigo no fim, como o MovieLens grava ("Matrix, The"); o modelo às vezes copia esse formato.
_TRAILING_ARTICLE = re.compile(
    r"^(?P<body>.+?),\s*(?P<art>the|a|an|les|le|la|l'|il|el|los|las|lo|die|der|das|den|det|de|o|os|as"
    r"|un|une|una|uno|i|gli|ett|en)$"
)


def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s).strip()
    m = _TRAILING_ARTICLE.match(s)
    if m:
        s = f"{m['art']} {m['body']}"
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _title_index() -> tuple[dict[str, list[tuple[int, int | None, int]]], list[str]]:
    index: dict[str, list[tuple[int, int | None, int]]] = {}
    conn = connect()
    for r in conn.execute("SELECT movie_id, display_title, year, n_ratings FROM movies"):
        keys = {normalize_title(r["display_title"])}
        for alt in re.findall(r"\((?:a\.k\.a\. )?(.*?)\)", r["display_title"]):
            keys.add(normalize_title(alt))
        for k in keys:
            if k:
                index.setdefault(k, []).append((r["movie_id"], r["year"], r["n_ratings"]))
    return index, list(index)


def match_title(title: str, year: int | None = None) -> int | None:
    """Encontra o movie_id de um título citado pelo usuário ou pelo modelo."""
    index, keys = _title_index()
    key = normalize_title(title)
    cands = index.get(key)
    if not cands:
        close = difflib.get_close_matches(key, keys, n=1, cutoff=0.88)
        cands = index.get(close[0]) if close else None
    if not cands:
        return None
    if year is not None:
        near = [c for c in cands if c[1] is not None and abs(c[1] - year) <= 1]
        if near:
            cands = near
    return max(cands, key=lambda c: c[2])[0]
