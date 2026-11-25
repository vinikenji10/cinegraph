"""Histórico de conversas e cota diária por usuário.

Em produção fica no Firestore (o navegador não acessa o banco diretamente:
as regras negam tudo e só o backend, com credencial de serviço, lê e grava).
No modo de desenvolvimento local, fica em memória.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Protocol


class QuotaExceeded(Exception):
    pass


class Storage(Protocol):
    def consume_quota(self, uid: str, limit: int) -> int: ...
    def history(self, uid: str, conversation_id: str, n: int = 3) -> list[dict]: ...
    def save_turn(self, uid: str, conversation_id: str, turn: dict) -> None: ...


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def new_conversation_id() -> str:
    return uuid.uuid4().hex[:20]


class MemoryStorage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, int] = {}
        self._turns: dict[tuple[str, str], list[dict]] = {}

    def consume_quota(self, uid, limit):
        with self._lock:
            key = f"{uid}_{_today()}"
            used = self._usage.get(key, 0)
            if used >= limit:
                raise QuotaExceeded
            self._usage[key] = used + 1
            return limit - used - 1

    def history(self, uid, conversation_id, n=3):
        return self._turns.get((uid, conversation_id), [])[-n:]

    def save_turn(self, uid, conversation_id, turn):
        with self._lock:
            self._turns.setdefault((uid, conversation_id), []).append(turn)


class FirestoreStorage:
    def __init__(self, project: str | None = None) -> None:
        from google.cloud import firestore

        self.fs = firestore
        self.client = firestore.Client(project=project)

    def consume_quota(self, uid, limit):
        ref = self.client.collection("usage").document(f"{uid}_{_today()}")
        transaction = self.client.transaction()

        @self.fs.transactional
        def _inc(tx):
            snap = ref.get(transaction=tx)
            used = (snap.to_dict() or {}).get("count", 0) if snap.exists else 0
            if used >= limit:
                raise QuotaExceeded
            tx.set(ref, {"uid": uid, "day": _today(), "count": used + 1})
            return limit - used - 1

        return _inc(transaction)

    def _conv(self, uid, conversation_id):
        return self.client.collection("users").document(uid).collection("conversations").document(conversation_id)

    def history(self, uid, conversation_id, n=3):
        q = (self._conv(uid, conversation_id).collection("turns")
             .order_by("created_at", direction=self.fs.Query.DESCENDING).limit(n))
        turns = [d.to_dict() for d in q.stream()]
        return list(reversed(turns))

    def save_turn(self, uid, conversation_id, turn):
        now = datetime.now(timezone.utc)
        conv = self._conv(uid, conversation_id)
        conv.set({"updated_at": now}, merge=True)
        conv.collection("turns").add({**turn, "created_at": now})
