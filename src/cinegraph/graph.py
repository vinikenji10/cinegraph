"""Grafo LangGraph do CineGraph.

    START ─┬─> classify ─┬─> sql ────┐
           │             ├─> vector ─┼─> answer ─> END
           │             ├─> hybrid ─┘
           │             └─> outro ────────────> END
           └─> baseline (só LLM, usado na avaliação) ─> END

O nó `classify` escolhe a rota e extrai filtros; cada nó de recuperação
registra o que fez (SQL, candidatos, latência), e o nó `answer` só pode citar
filmes recuperados. Título, ano e nota exibidos vêm sempre do banco.
"""

from __future__ import annotations


import operator
import time
from typing import Annotated, Any, TypedDict

import numpy as np
from langgraph.graph import END, START, StateGraph

from cinegraph import db, prompts
from cinegraph.llm import LLM, LLMError, get_llm
from cinegraph.retrieval.embedder import embed_query
from cinegraph.retrieval.vectorstore import combined_score, cosine_from_score, get_vector_store
from cinegraph.schemas import Filters, RoutePlan
from cinegraph.sqltool import SQLValidationError, validate_sql

VECTOR_K = 60
HYBRID_K = 200
MAX_K = 1000
MAX_CANDIDATES = 15
FORCEABLE = {"sql", "vector", "hybrid", "llm_only"}


class State(TypedDict, total=False):
    question: str
    history: list[dict]
    force_route: str | None
    plan: RoutePlan
    route: str
    sql: str | None
    sql_explanation: str | None
    sql_error: str | None
    columns: list[str]
    rows: list[list[Any]]
    candidates: list[dict]
    records: dict[int, dict]
    retrieval: dict
    answer: dict
    calls: Annotated[list[dict], operator.add]
    steps: Annotated[list[dict], operator.add]


def _step(name: str, t0: float, **extra) -> list[dict]:
    return [{"node": name, "latency_s": round(time.perf_counter() - t0, 3), **extra}]


def _calls(*calls) -> list[dict]:
    return [c.to_dict() for c in calls if c is not None]


def _previous_ids(history: list[dict]) -> list[int]:
    return [m["movie_id"] for m in history[-1].get("movies", [])] if history else []


def _compact(rec: dict, similarity: float | None = None) -> dict:
    out = {k: rec[k] for k in ("movie_id", "title", "year", "genres", "avg_rating", "n_ratings", "bayes_rating")}
    out["tags"] = rec["tags"][:10]
    if similarity is not None:
        out["similarity"] = round(similarity, 3)
    return out


def _clamp_plan(plan: RoutePlan) -> RoutePlan:
    plan.limit = max(1, min(int(plan.limit or 5), 20))
    return plan


def build_graph(llm: LLM, vector_backend: str | None = None):
    def classify(state: State) -> dict:
        t0 = time.perf_counter()
        plan, call = state.get("plan"), None
        if plan is None:
            plan, call = llm.route(state["question"], state.get("history", []))
        plan = _clamp_plan(plan)
        route = state.get("force_route") or plan.route
        return {"plan": plan, "route": route, "calls": _calls(call),
                "steps": _step("classify", t0, route=route, forced=bool(state.get("force_route")))}

    def sql_node(state: State) -> dict:
        t0 = time.perf_counter()
        plan, q, history = state["plan"], state["question"], state.get("history", [])
        calls, error, sql, explanation = [], None, None, None
        columns, rows = [], []
        for attempt in range(2):
            draft, call = llm.draft_sql(q, plan, history, _previous_ids(history),
                                        error=error if attempt else None, failed_sql=sql if attempt else None)
            calls.append(call)
            explanation = draft.explanation
            try:
                sql = validate_sql(draft.sql)
                columns, rows, _ = db.run_query(sql, max_rows=50)
                error = None
                break
            except (SQLValidationError, Exception) as e:  # noqa: BLE001 - erro volta ao modelo uma vez
                sql = sql or draft.sql
                error = str(e)
        ids: list[int] = []
        if "movie_id" in columns:
            idx = columns.index("movie_id")
            ids = [int(r[idx]) for r in rows if r[idx] is not None]
            ids = list(dict.fromkeys(ids))
        records = db.fetch_movies(ids)
        candidates = [{"movie_id": i, "similarity": None, "score": None} for i in ids if i in records]
        return {
            "sql": sql, "sql_explanation": explanation, "sql_error": error,
            "columns": columns, "rows": [list(r) for r in rows],
            "candidates": candidates, "records": records,
            "calls": _calls(*calls), "steps": _step("sql", t0, n_rows=len(rows), error=error),
        }

    def _semantic(state: State, hybrid: bool) -> dict:
        t0 = time.perf_counter()
        plan, history = state["plan"], state.get("history", [])
        store = get_vector_store(vector_backend)
        query_text = plan.semantic_query or state["question"]
        v = embed_query(query_text)
        ref_matches = [(t, db.match_title(t)) for t in plan.reference_titles]
        ref_ids = list(dict.fromkeys(m for _, m in ref_matches if m is not None))
        ref_unmatched = [t for t, m in ref_matches if m is None]
        ref_vecs = [x for x in (store.get_vector(r) for r in ref_ids) if x is not None]
        if ref_vecs:
            v = v + np.mean(ref_vecs, axis=0)
            v = (v / np.linalg.norm(v)).astype(np.float32)

        filters = plan.filters if hybrid else Filters.empty()
        prev = _previous_ids(history) if plan.use_previous_results else []
        searched_k = 0
        if prev:
            prev_info = db.fetch_movies(prev)
            pool = []
            for mid in prev:
                vec = store.get_vector(mid)
                if vec is not None and mid in prev_info:
                    pool.append((mid, combined_score(float(vec @ v), prev_info[mid]["n_ratings"])))
            pool.sort(key=lambda x: (-x[1], x[0]))
        else:
            searched_k = HYBRID_K if (hybrid and not filters.is_empty()) else VECTOR_K
            pool = store.search(v, searched_k)
        pool = [(m, s) for m, s in pool if m not in ref_ids]

        passing = {m for m, _ in pool}
        if not filters.is_empty():
            passing = db.filter_ids([m for m, _ in pool], filters)
            if len(passing) < plan.limit and not prev and searched_k < MAX_K:
                searched_k = MAX_K
                pool = [(m, s) for m, s in store.search(v, MAX_K) if m not in ref_ids]
                passing = db.filter_ids([m for m, _ in pool], filters)
        pool = [(m, s) for m, s in pool if m in passing]

        # O score do banco já combina similaridade e popularidade (ver vectorstore.py).
        info = db.fetch_movies([m for m, _ in pool])
        scored = [(m, cosine_from_score(sc, info[m]["n_ratings"]), sc) for m, sc in pool if m in info]
        if plan.sort_by != "relevance":
            top = scored[:30]
            keyf = {
                "rating": lambda x: -(info[x[0]]["bayes_rating"] or 0),
                "popularity": lambda x: -info[x[0]]["n_ratings"],
                "newest": lambda x: -(info[x[0]]["year"] or 0),
                "oldest": lambda x: (info[x[0]]["year"] or 9999),
            }[plan.sort_by]
            scored = sorted(top, key=keyf)
        n_keep = min(MAX_CANDIDATES, max(plan.limit * 2, 10))
        kept = scored[:n_keep]
        candidates = [{"movie_id": m, "similarity": round(s, 4), "score": round(sc, 4)} for m, s, sc in kept]
        records = {m: info[m] for m, _, _ in kept}
        retrieval = {
            "backend": store.name, "query_text": query_text, "reference_ids": ref_ids,
            "reference_unmatched": ref_unmatched, "searched_k": searched_k, "pool_after_filters": len(pool), "used_previous": bool(prev),
        }
        name = "hybrid" if hybrid else "vector"
        return {"candidates": candidates, "records": records, "retrieval": retrieval,
                "steps": _step(name, t0, **{k: retrieval[k] for k in ("searched_k", "pool_after_filters")})}

    def vector_node(state: State) -> dict:
        return _semantic(state, hybrid=False)

    def hybrid_node(state: State) -> dict:
        return _semantic(state, hybrid=True)

    def answer_node(state: State) -> dict:
        t0 = time.perf_counter()
        plan, route = state["plan"], state["route"]
        candidates, records = state.get("candidates", []), state.get("records", {})
        rows = state.get("rows", [])

        if route == "sql" and state.get("sql_error") and not rows:
            ans = {"text": "Não consegui montar uma consulta válida para essa pergunta. Pode reformular?",
                   "recommendations": [], "numeric_answer": None, "dropped_ids": []}
            return {"answer": ans, "steps": _step("answer", t0, llm=False)}
        if not candidates and not rows:
            ans = {"text": "Não encontrei filmes no acervo que atendam a todos os critérios. "
                           "Tente ampliar o período, a nota mínima ou os gêneros.",
                   "recommendations": [], "numeric_answer": None, "dropped_ids": []}
            return {"answer": ans, "steps": _step("answer", t0, llm=False)}

        payload: dict[str, Any] = {}
        if route == "sql":
            payload["sql_result"] = {"columns": state.get("columns", []), "rows": rows[:30]}
        payload["candidates"] = [_compact(records[c["movie_id"]], c.get("similarity")) for c in candidates]
        # Filmes citados como referência ("algo como Matrix") saem dos candidatos de propósito;
        # a redação precisa saber disso para usá-los na explicação sem dizer que "faltam".
        retrieval = state.get("retrieval") or {}
        ref_info = db.fetch_movies(retrieval.get("reference_ids", []))
        if ref_info:
            payload["reference_movies"] = [_compact(r) for r in ref_info.values()]
        if retrieval.get("reference_unmatched"):
            payload["reference_not_in_collection"] = retrieval["reference_unmatched"]
        draft, call = llm.answer(state["question"], route, payload, plan.limit)

        valid = [c["movie_id"] for c in candidates]
        reasons = {r.movie_id: r.reason for r in draft.recommendations}
        dropped = [r.movie_id for r in draft.recommendations if r.movie_id not in records]
        if route == "sql":
            chosen = valid  # a ordem do SQL é a resposta; o modelo só redige as justificativas
        else:
            chosen = [m for m in dict.fromkeys(r.movie_id for r in draft.recommendations) if m in records]
            chosen = chosen[: plan.limit]
        recs = [{**records[m], "reason": reasons.get(m, "")} for m in chosen]
        ans = {"text": draft.text, "recommendations": recs, "numeric_answer": draft.numeric_answer,
               "dropped_ids": dropped}
        return {"answer": ans, "calls": _calls(call), "steps": _step("answer", t0, llm=True, dropped=len(dropped))}

    def outro_node(state: State) -> dict:
        t0 = time.perf_counter()
        ans = {"text": prompts.OUT_OF_SCOPE_TEXT, "recommendations": [], "numeric_answer": None, "dropped_ids": []}
        return {"answer": ans, "steps": _step("outro", t0)}

    def baseline_node(state: State) -> dict:
        t0 = time.perf_counter()
        draft, call = llm.baseline(state["question"])
        recs = []
        for r in draft.recommendations:
            mid = db.match_title(r.title, r.year)
            rec = db.fetch_movies([mid]).get(mid) if mid is not None else None
            checks = {"exists": rec is not None}
            if rec:
                checks["year_ok"] = r.year is None or rec["year"] is None or abs(r.year - rec["year"]) <= 1
                claimed = {g for g in r.genres if g in set(prompts.GENRES_PT)}
                checks["genres_ok"] = claimed <= set(rec["genres"])
                checks["rating_ok"] = (r.avg_rating is None or rec["avg_rating"] is None
                                       or abs(r.avg_rating - rec["avg_rating"]) <= 0.3)
            recs.append({"claimed": r.model_dump(), "movie_id": mid, "record": rec, "checks": checks,
                         "reason": r.reason})
        ans = {"text": draft.text, "recommendations": recs, "numeric_answer": draft.numeric_answer,
               "dropped_ids": []}
        return {"route": "llm_only", "answer": ans, "calls": _calls(call), "steps": _step("baseline", t0)}

    g = StateGraph(State)
    g.add_node("classify", classify)
    g.add_node("sql", sql_node)
    g.add_node("vector", vector_node)
    g.add_node("hybrid", hybrid_node)
    g.add_node("answer", answer_node)
    g.add_node("outro", outro_node)
    g.add_node("baseline", baseline_node)
    g.add_conditional_edges(
        START, lambda s: "baseline" if s.get("force_route") == "llm_only" else "classify",
        {"baseline": "baseline", "classify": "classify"},
    )
    g.add_conditional_edges(
        "classify", lambda s: s["route"],
        {"sql": "sql", "vector": "vector", "hybrid": "hybrid", "outro": "outro"},
    )
    for n in ("sql", "vector", "hybrid"):
        g.add_edge(n, "answer")
    g.add_edge("answer", END)
    g.add_edge("outro", END)
    g.add_edge("baseline", END)
    return g.compile()


_graphs: dict[tuple[int, str | None], Any] = {}


def ask(question: str, history: list[dict] | None = None, force_route: str | None = None,
        plan: RoutePlan | None = None, llm: LLM | None = None, vector_backend: str | None = None) -> dict:
    """Roda o grafo e devolve resposta + rastro completo (rota, SQL, candidatos, custo, latência)."""
    if force_route is not None and force_route not in FORCEABLE:
        raise ValueError(f"Rota forçada inválida: {force_route}")
    llm = llm or get_llm()
    key = (id(llm), vector_backend)
    if key not in _graphs:
        _graphs[key] = build_graph(llm, vector_backend)
    t0 = time.perf_counter()
    init: State = {"question": question.strip(), "history": history or [], "force_route": force_route,
                   "calls": [], "steps": []}
    if plan is not None:
        init["plan"] = plan
    error = None
    try:
        state = _graphs[key].invoke(init)
    except LLMError as e:
        state, error = {**init, "answer": {"text": f"Erro ao consultar o modelo: {e}", "recommendations": [],
                                           "numeric_answer": None, "dropped_ids": []}}, str(e)
    calls = state.get("calls", [])
    plan_obj = state.get("plan")
    return {
        "question": question,
        "route": state.get("route"),
        "forced": force_route,
        "plan": plan_obj.model_dump() if plan_obj is not None else None,
        "sql": state.get("sql"),
        "sql_explanation": state.get("sql_explanation"),
        "sql_error": state.get("sql_error"),
        "sql_columns": state.get("columns"),
        "sql_rows": state.get("rows"),
        "retrieval": state.get("retrieval"),
        "candidates": state.get("candidates"),
        "answer": state["answer"],
        "steps": state.get("steps", []),
        "calls": calls,
        "latency_s": round(time.perf_counter() - t0, 3),
        "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
        "error": error,
    }
