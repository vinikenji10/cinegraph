"""API HTTP do CineGraph (FastAPI), servida no Cloud Run atrás do Firebase Hosting.

Segurança:
- Toda chamada a /api/chat exige um ID token do Firebase Auth (login Google).
- Só e-mails verificados da lista CINEGRAPH_ALLOWED_EMAILS ou dos domínios
  CINEGRAPH_ALLOWED_DOMAINS entram; com as duas vazias, ninguém entra.
- Cada usuário tem uma cota diária (CINEGRAPH_DAILY_LIMIT) para limitar gasto com a API.
- CINEGRAPH_DEV_NO_AUTH=1 desliga o login só fora do Cloud Run (desenvolvimento local).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cinegraph.config import PROJECT_ROOT, get_settings
from cinegraph.graph import ask
from cinegraph.storage import FirestoreStorage, MemoryStorage, QuotaExceeded, Storage, new_conversation_id

log = logging.getLogger("cinegraph.api")
logging.basicConfig(level=logging.INFO)

ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))
DEV_NO_AUTH = os.environ.get("CINEGRAPH_DEV_NO_AUTH") == "1" and not ON_CLOUD_RUN
DAILY_LIMIT = int(os.environ.get("CINEGRAPH_DAILY_LIMIT", "60"))
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("CINEGRAPH_ALLOWED_EMAILS", "").split(",") if e.strip()}
ALLOWED_DOMAINS = {d.strip().lower().lstrip("@") for d in os.environ.get("CINEGRAPH_ALLOWED_DOMAINS", "").split(",") if d.strip()}

app = FastAPI(title="CineGraph API", docs_url=None, redoc_url=None)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return MemoryStorage() if DEV_NO_AUTH else FirestoreStorage(project=get_settings().gcp_project)


@lru_cache(maxsize=1)
def _firebase_app():
    import firebase_admin

    project = get_settings().gcp_project
    return firebase_admin.initialize_app(options={"projectId": project} if project else None)


def is_allowed(email: str) -> bool:
    email = email.lower()
    return email in ALLOWED_EMAILS or email.rsplit("@", 1)[-1] in ALLOWED_DOMAINS


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if DEV_NO_AUTH:
        return {"uid": "dev", "email": "dev@localhost", "name": "Modo local"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para usar o CineGraph.")
    from firebase_admin import auth

    try:
        decoded = auth.verify_id_token(authorization.split(" ", 1)[1], app=_firebase_app())
    except Exception as e:  # noqa: BLE001
        log.info("token inválido: %s", type(e).__name__)
        raise HTTPException(401, "Sessão inválida ou expirada. Entre de novo.") from e
    email = decoded.get("email", "")
    if not decoded.get("email_verified") or not is_allowed(email):
        raise HTTPException(403, "Sua conta não está na lista de acesso do CineGraph.")
    return decoded


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    conversation_id: str | None = Field(default=None, max_length=40)


CARD_FIELDS = ("movie_id", "title", "year", "genres", "avg_rating", "n_ratings", "bayes_rating",
               "tags", "imdb_id", "tmdb_id", "reason")


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "model": s.llm_model, "vector_backend": s.vector_backend, "auth": not DEV_NO_AUTH}


@app.get("/api/me")
def me(user: dict = Depends(current_user)) -> dict:
    return {"email": user.get("email"), "name": user.get("name"), "daily_limit": DAILY_LIMIT}


@app.post("/api/chat")
def chat(req: ChatRequest, user: dict = Depends(current_user)) -> dict:
    storage = get_storage()
    uid = user["uid"]
    try:
        remaining = storage.consume_quota(uid, DAILY_LIMIT)
    except QuotaExceeded:
        raise HTTPException(429, f"Limite diário de {DAILY_LIMIT} perguntas atingido. Volte amanhã.")
    conversation_id = req.conversation_id or new_conversation_id()
    history = storage.history(uid, conversation_id) if req.conversation_id else []
    result = ask(req.message, history=history)
    answer = result["answer"]
    cards = [{k: r.get(k) for k in CARD_FIELDS} for r in answer["recommendations"]]
    storage.save_turn(uid, conversation_id, {
        "question": req.message,
        "answer": answer["text"],
        "movies": [{"movie_id": c["movie_id"], "title": c["title"], "year": c["year"]} for c in cards],
        "route": result["route"],
        "sql": result["sql"],
        "latency_s": result["latency_s"],
        "cost_usd": result["cost_usd"],
        "error": result["error"],
    })
    log.info("chat uid=%s route=%s latency=%.2fs cost=%.4f", uid[:6], result["route"], result["latency_s"], result["cost_usd"])
    return {
        "conversation_id": conversation_id,
        "remaining_today": remaining,
        "answer": {"text": answer["text"], "numeric_answer": answer.get("numeric_answer"), "movies": cards},
        "trace": {
            "route": result["route"],
            "plan": result["plan"],
            "sql": result["sql"],
            "sql_explanation": result["sql_explanation"],
            "sql_error": result["sql_error"],
            "retrieval": result["retrieval"],
            "steps": result["steps"],
            "tokens": {
                "input": sum(c["input_tokens"] + c["cache_read_tokens"] + c["cache_write_tokens"] for c in result["calls"]),
                "output": sum(c["output_tokens"] for c in result["calls"]),
            },
            "latency_s": result["latency_s"],
            "cost_usd": result["cost_usd"],
            "error": result["error"],
        },
    }


# Em desenvolvimento local, a própria API serve a página (no ar, quem serve é o Firebase Hosting).
WEB_DIR = Path(os.environ.get("CINEGRAPH_WEB_DIR", PROJECT_ROOT / "web"))
if not ON_CLOUD_RUN and WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/{name}.{ext}")
    def web_file(name: str, ext: str) -> FileResponse:
        path = (WEB_DIR / f"{name}.{ext}").resolve()
        if path.parent != WEB_DIR.resolve() or not path.exists():
            raise HTTPException(404)
        return FileResponse(path)
