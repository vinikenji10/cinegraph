"""Configuração central do CineGraph.

Todos os caminhos e parâmetros vêm daqui, com sobrescrita por variáveis de
ambiente. Localmente, um arquivo `.env` na raiz do projeto é lido; no Cloud Run
as variáveis são definidas no deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(os.environ.get("CINEGRAPH_HOME", Path(__file__).resolve().parents[2]))
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(os.environ.get("CINEGRAPH_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

DATASET_NAME = "ml-latest-small"
DATASET_URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
DATASET_MD5_URL = DATASET_URL + ".md5"

# Gêneros do MovieLens, com tradução usada no prompt e na interface.
GENRES_PT: dict[str, str] = {
    "Action": "ação",
    "Adventure": "aventura",
    "Animation": "animação",
    "Children": "infantil",
    "Comedy": "comédia",
    "Crime": "crime",
    "Documentary": "documentário",
    "Drama": "drama",
    "Fantasy": "fantasia",
    "Film-Noir": "film noir",
    "Horror": "terror",
    "IMAX": "IMAX",
    "Musical": "musical",
    "Mystery": "mistério",
    "Romance": "romance",
    "Sci-Fi": "ficção científica",
    "Thriller": "suspense",
    "War": "guerra",
    "Western": "faroeste",
}

# Média bayesiana: WR = v/(v+m)·R + m/(v+m)·C. "m" é o número mínimo de
# avaliações para a média do filme pesar tanto quanto a média global.
BAYES_M = 10


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("CINEGRAPH_DB_PATH", PROCESSED_DIR / "cinegraph.db"))
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("CINEGRAPH_LLM_MODEL", "claude-sonnet-5-5"))
    embed_model: str = field(
        default_factory=lambda: os.environ.get(
            "CINEGRAPH_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    )
    embed_cache_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("FASTEMBED_CACHE_PATH", DATA_DIR / "models"))
    )
    # "local" usa a matriz numpy em data/processed; "firestore" usa a busca vetorial do Firestore.
    vector_backend: str = field(default_factory=lambda: os.environ.get("CINEGRAPH_VECTOR_BACKEND", "local"))
    gcp_project: str | None = field(default_factory=lambda: os.environ.get("GOOGLE_CLOUD_PROJECT"))
    firestore_collection: str = field(
        default_factory=lambda: os.environ.get("CINEGRAPH_FIRESTORE_COLLECTION", "movies")
    )
    max_sql_rows: int = 50

    @property
    def embeddings_path(self) -> Path:
        return PROCESSED_DIR / "embeddings.npy"

    @property
    def embedding_ids_path(self) -> Path:
        return PROCESSED_DIR / "embedding_ids.npy"

    @property
    def profiles_path(self) -> Path:
        return PROCESSED_DIR / "profiles.jsonl"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
