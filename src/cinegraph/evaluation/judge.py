"""Juiz de relevância (LLM-as-judge) para perguntas semânticas e mistas.

O juiz vê a pergunta, a intenção descrita no conjunto de teste e os filmes
recomendados (com dados do banco) e dá 0, 1 ou 2 a cada um. Ele pode usar o
próprio conhecimento sobre os filmes, como um avaliador humano faria, porque
as tags do MovieLens cobrem poucos filmes. Filmes inexistentes recebem 0 sem
passar pelo juiz.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from cinegraph.llm import ClaudeLLM, LLMCall

JUDGE_PROMPT_VERSION = "2026-11-10.1"

JUDGE_SYSTEM = """\
Você é um avaliador rigoroso de recomendações de filmes. Recebe um pedido de usuário, a intenção
por trás dele e uma lista de filmes recomendados. Para cada filme, atribua:
  2 = atende claramente ao pedido;
  1 = atende em parte (relação fraca ou só um aspecto do pedido);
  0 = não atende, ou é um filme que o pedido manda excluir.
Avalie só a adequação temática/atmosférica ao pedido. Restrições objetivas (ano, gênero, nota) são
verificadas por outro processo: ignore-as. Use seu conhecimento sobre os filmes além dos dados
fornecidos. Seja consistente: o mesmo filme para o mesmo pedido recebe a mesma nota.
"""


class JudgeScore(BaseModel):
    index: int
    relevance: int = Field(description="0, 1 ou 2")
    note: str = Field(description="Justificativa em poucas palavras")


class JudgeOut(BaseModel):
    scores: list[JudgeScore]


class Judge:
    def __init__(self, llm: ClaudeLLM | None = None) -> None:
        self.llm = llm or ClaudeLLM()

    def score(self, question: str, intent: str, items: list[dict]) -> tuple[list[int], list[str], LLMCall | None]:
        """items: [{title, year, genres, tags}] — devolve notas na mesma ordem."""
        if not items:
            return [], [], None
        payload = [{"index": i, **it} for i, it in enumerate(items)]
        user = (f"Pedido: {question}\nIntenção: {intent}\n\nFilmes recomendados (JSON):\n"
                f"{json.dumps(payload, ensure_ascii=False)}")
        out, call = self.llm._call("judge", JUDGE_SYSTEM, user, JudgeOut, effort="low", max_tokens=4000)
        by_idx = {s.index: s for s in out.scores}
        rel = [max(0, min(2, int(by_idx[i].relevance))) if i in by_idx else 0 for i in range(len(items))]
        notes = [by_idx[i].note if i in by_idx else "sem nota" for i in range(len(items))]
        return rel, notes, call
