"""Camada do modelo de linguagem (Claude Sonnet 5.5 pelo SDK oficial da Anthropic).

Cada chamada usa saída estruturada (Pydantic), cache de prompt no texto de
sistema e fallback do servidor para recusas. O grafo depende só do protocolo
`LLM`, o que permite testar tudo com um modelo falso, sem custo.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Protocol, TypeVar

from pydantic import BaseModel

from cinegraph import prompts
from cinegraph.config import get_settings
from cinegraph.schemas import AnswerDraft, BaselineAnswer, RoutePlan, SQLDraft

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US$ por milhão de tokens (Claude Sonnet 5.5, API da Anthropic).
PRICES = {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMCall:
    name: str
    model: str
    latency_s: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens * PRICES["input"]
            + self.output_tokens * PRICES["output"]
            + self.cache_write_tokens * PRICES["cache_write"]
            + self.cache_read_tokens * PRICES["cache_read"]
        ) / 1e6

    def to_dict(self) -> dict:
        return {**asdict(self), "cost_usd": round(self.cost_usd, 6)}


class LLM(Protocol):
    def route(self, question: str, history: list[dict]) -> tuple[RoutePlan, LLMCall | None]: ...
    def draft_sql(self, question: str, plan: RoutePlan, history: list[dict],
                  previous_ids: list[int], error: str | None = None,
                  failed_sql: str | None = None) -> tuple[SQLDraft, LLMCall | None]: ...
    def answer(self, question: str, route: str, payload: dict, limit: int) -> tuple[AnswerDraft, LLMCall | None]: ...
    def baseline(self, question: str) -> tuple[BaselineAnswer, LLMCall | None]: ...


def _history_block(history: list[dict]) -> str:
    if not history:
        return "(início da conversa)"
    lines = []
    for turn in history[-3:]:
        movies = "; ".join(f"[{m['movie_id']}] {m['title']} ({m.get('year')})" for m in turn.get("movies", []))
        lines.append(f"Usuário: {turn['question']}\nCineGraph: {turn.get('answer', '')}\nFilmes mostrados: {movies or '-'}")
    return "\n\n".join(lines)


class ClaudeLLM:
    def __init__(self, model: str | None = None) -> None:
        import anthropic

        self.client = anthropic.Anthropic(max_retries=3, timeout=90)
        self.model = model or get_settings().llm_model
        self.use_fallback = os.environ.get("CINEGRAPH_LLM_FALLBACK", "1") != "0"

    def _call(self, name: str, system: str, user: str, schema: type[T], effort: str,
              max_tokens: int = 8000) -> tuple[T, LLMCall]:
        import anthropic

        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
        )
        if self.use_fallback:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        t0 = time.perf_counter()
        try:
            resp = self.client.beta.messages.parse(**kwargs)
        except anthropic.APIStatusError as e:
            raise LLMError(f"Erro da API ({e.status_code}) em {name}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Falha de conexão com a API em {name}") from e
        latency = time.perf_counter() - t0
        if resp.stop_reason == "refusal":
            raise LLMError(f"O modelo recusou a etapa {name}.")
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"Resposta truncada na etapa {name} (max_tokens).")
        parsed = resp.parsed_output
        if parsed is None:
            raise LLMError(f"Saída estruturada ausente na etapa {name}.")
        u = resp.usage
        call = LLMCall(
            name=name,
            model=resp.model,
            latency_s=round(latency, 3),
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return parsed, call

    def route(self, question, history):
        user = f"Contexto da conversa:\n{_history_block(history)}\n\nPergunta atual: {question}"
        return self._call("route", prompts.ROUTER_SYSTEM, user, RoutePlan, effort="low", max_tokens=4000)

    def draft_sql(self, question, plan, history, previous_ids, error=None, failed_sql=None):
        parts = [f"Contexto da conversa:\n{_history_block(history)}", f"Pergunta: {question}",
                 f"Quantidade pedida: {plan.limit}"]
        if plan.use_previous_results and previous_ids:
            parts.append(f"IDs da resposta anterior: {previous_ids}")
        if error:
            parts.append(f"A consulta anterior falhou.\nSQL: {failed_sql}\nErro: {error}\nCorrija.")
        return self._call("sql", prompts.SQL_SYSTEM, "\n\n".join(parts), SQLDraft, effort="medium")

    def answer(self, question, route, payload, limit):
        user = (
            f"Pergunta: {question}\nRota: {route}\nQuantidade pedida: {limit}\n\n"
            f"Dados recuperados (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
        return self._call("answer", prompts.ANSWER_SYSTEM, user, AnswerDraft, effort="low")

    def baseline(self, question):
        return self._call("baseline", prompts.BASELINE_SYSTEM, f"Pergunta: {question}", BaselineAnswer, effort="low")


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    return ClaudeLLM()
