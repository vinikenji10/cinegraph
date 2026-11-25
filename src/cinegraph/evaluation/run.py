"""Executa a avaliação: `uv run cinegraph eval [--limit N] [--configs ...] [--no-judge]`.

Configurações comparadas (hipóteses do slide "Etapas do projeto"):
    routed    sistema completo (o grafo escolhe a rota)
    sql       força Text-to-SQL em todas as perguntas
    vector    força busca vetorial
    hybrid    força o modo híbrido
    llm_only  baseline sem recuperação (só o conhecimento do modelo)

A classificação (rota + filtros) é feita uma vez por pergunta e reaproveitada
nas configurações forçadas, que só trocam o mecanismo de recuperação. A busca
vetorial usa o backend local (mesmos vetores do Firestore), para não gastar a
cota diária de leituras; `compare_backends` documenta que os resultados são iguais.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cinegraph.config import PROJECT_ROOT, get_settings
from cinegraph.evaluation import scoring
from cinegraph.evaluation.judge import JUDGE_PROMPT_VERSION, Judge
from cinegraph.graph import ask
from cinegraph.llm import get_llm
from cinegraph.prompts import PROMPT_VERSION
from cinegraph.schemas import RoutePlan

QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.yaml"
REPORTS_DIR = PROJECT_ROOT / "reports"
ALL_CONFIGS = ["routed", "sql", "vector", "hybrid", "llm_only"]


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text())


def _compact_recs(res: dict, config: str) -> list[dict]:
    recs = []
    for r in res["answer"]["recommendations"]:
        if config == "llm_only":
            rec = r.get("record")
            recs.append({"movie_id": r.get("movie_id"), "title": r["claimed"]["title"], "claimed": r["claimed"],
                         "checks": r.get("checks"), "record": _small(rec) if rec else None})
        else:
            recs.append({"movie_id": r["movie_id"], "title": r["title"], "record": _small(r)})
    return recs


def _small(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("movie_id", "title", "year", "genres", "avg_rating", "n_ratings", "tags")}


def run_one(q: dict, config: str, plan: RoutePlan | None) -> dict:
    t0 = time.perf_counter()
    if config == "routed":
        res = ask(q["question"], vector_backend="local")
    elif config == "llm_only":
        res = ask(q["question"], force_route="llm_only", vector_backend="local")
    else:
        res = ask(q["question"], force_route=config, plan=plan, vector_backend="local")
    return {
        "qid": q["id"], "config": config, "category": q["category"],
        "route": res["route"], "plan": res["plan"],
        "sql": res["sql"], "sql_error": res["sql_error"],
        "sql_columns": res["sql_columns"], "sql_rows": (res["sql_rows"] or [])[:50],
        "retrieval": res["retrieval"],
        "text": res["answer"]["text"], "numeric_answer": res["answer"].get("numeric_answer"),
        "recs": _compact_recs(res, config), "dropped_ids": res["answer"].get("dropped_ids", []),
        "latency_s": res["latency_s"], "cost_usd": res["cost_usd"], "calls": res["calls"],
        "error": res["error"], "wall_s": round(time.perf_counter() - t0, 3),
    }


def _judge_items(r: dict) -> tuple[list[dict], list[int]]:
    items, positions = [], []
    for i, rec in enumerate(r["recs"]):
        data = rec.get("record")
        if data:
            items.append({k: data.get(k) for k in ("title", "year", "genres", "tags")})
            positions.append(i)
    return items, positions


def run_eval(configs: list[str] | None = None, limit: int | None = None, ids: list[str] | None = None,
             judge: bool = True, workers: int = 4) -> Path:
    configs = [c for c in (configs or ALL_CONFIGS) if c in ALL_CONFIGS]
    questions = load_questions()
    if ids:
        questions = [q for q in questions if q["id"] in set(ids)]
    if limit:
        # amostra equilibrada: as N primeiras de cada categoria
        by_cat: dict[str, list] = {}
        for q in questions:
            by_cat.setdefault(q["category"], []).append(q)
        questions = [q for qs in by_cat.values() for q in qs[:limit]]
    qmap = {q["id"]: q for q in questions}
    get_llm()  # falha cedo se a chave da API não estiver configurada

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = REPORTS_DIR / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Avaliação: {len(questions)} perguntas × {configs} → {run_dir}")

    golds = {q["id"]: scoring.gold_answer(q) for q in questions if q["category"] == "sql"}
    results: list[dict] = []
    plans: dict[str, RoutePlan] = {}

    def jobs_for(config):
        for q in questions:
            if q["category"] == "outro" and config != "routed":
                continue
            yield q

    ordered = (["routed"] if "routed" in configs else []) + [c for c in configs if c != "routed"]
    for config in ordered:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_one, q, config, plans.get(q["id"])): q for q in jobs_for(config)}
            for fut in as_completed(futs):
                r = fut.result()
                if config == "routed" and r["plan"]:
                    plans[r["qid"]] = RoutePlan.model_validate(r["plan"])
                results.append(r)
        done = [r for r in results if r["config"] == config]
        print(f"  {config:9s} {len(done):3d} respostas em {time.perf_counter() - t0:5.1f}s "
              f"· US$ {sum(r['cost_usd'] for r in done):.3f} · erros {sum(1 for r in done if r['error'])}")

    if judge:
        judge_obj = Judge()
        to_judge = [r for r in results if r["category"] in ("semantic", "mixed")]
        t0 = time.perf_counter()

        def _do(r):
            items, pos = _judge_items(r)
            rel_full = [0] * len(r["recs"])
            notes_full = ["inexistente no acervo"] * len(r["recs"])
            if items:
                q = qmap[r["qid"]]
                rel, notes, call = judge_obj.score(q["question"], q.get("intent", ""), items)
                for p, v, n in zip(pos, rel, notes):
                    rel_full[p], notes_full[p] = v, n
                r["judge_cost_usd"] = call.cost_usd if call else 0.0
            r["relevance"], r["relevance_notes"] = rel_full, notes_full

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_do, to_judge))
        print(f"  juiz      {len(to_judge):3d} avaliações em {time.perf_counter() - t0:5.1f}s "
              f"· US$ {sum(r.get('judge_cost_usd', 0) for r in to_judge):.3f}")

    for r in results:
        q = qmap[r["qid"]]
        if q["category"] == "sql":
            r["scores"] = scoring.score_sql_question(q, r, golds[q["id"]])
        elif q["category"] in ("semantic", "mixed") and "relevance" in r:
            r["scores"] = scoring.score_judged(q, r)
        else:
            r["scores"] = {}
        r["hallucination"] = scoring.hallucination_counts(r)

    metrics = scoring.aggregate(qmap, results)
    s = get_settings()
    metrics["meta"] = {
        "run": stamp, "n_questions": len(questions), "configs": configs, "judge": judge,
        "llm_model": s.llm_model, "embed_model": s.embed_model, "vector_backend": "local",
        "prompt_version": PROMPT_VERSION, "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "total_cost_usd": round(sum(r["cost_usd"] for r in results)
                                + sum(r.get("judge_cost_usd", 0) for r in results), 4),
    }
    with (run_dir / "results.jsonl").open("w") as f:
        for r in sorted(results, key=lambda x: (x["config"], x["qid"])):
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    from cinegraph.evaluation.report import write_report

    summary = write_report(metrics, results, qmap, run_dir)
    print(f"Custo total: US$ {metrics['meta']['total_cost_usd']:.3f}\nRelatório: {summary}")
    return run_dir
