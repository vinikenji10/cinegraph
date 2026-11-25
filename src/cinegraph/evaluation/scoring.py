"""Pontuação de cada resposta contra o gabarito e agregação das métricas."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np

from cinegraph import db
from cinegraph.schemas import Filters

K = 5  # tamanho da lista avaliada em semânticas e mistas
EXPECTED_ROUTE = {"sql": "sql", "semantic": "vector", "mixed": "hybrid", "outro": "outro"}


# ---------------------------------------------------------------- utilidades

def numbers_in_text(text: str) -> list[float]:
    """Extrai números escritos em pt-BR ou en ('100.836', '4,2', '3.5')."""
    out = []
    for tok in re.findall(r"\d[\d.,]*", text or ""):
        tok = tok.rstrip(".,")
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", tok):
            tok = tok.replace(".", "")
        elif re.fullmatch(r"\d{1,3}(,\d{3})+", tok):
            tok = tok.replace(",", "")
        else:
            tok = tok.replace(",", ".")
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.011, 0.005 * abs(b))


def gold_answer(q: dict) -> dict:
    cols, rows, _ = db.run_query(q["gold_sql"], max_rows=100)
    if q["compare"] == "ordered":
        return {"ids": [int(r[0]) for r in rows]}
    return {"value": rows[0][0]}


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 42) -> tuple[float, float, float]:
    if not values:
        return float("nan"), float("nan"), float("nan")
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(arr, size=(n, len(arr)), replace=True).mean(axis=1)
    return float(arr.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ---------------------------------------------------------------- por resposta

def recommended_ids(res: dict) -> list[int | None]:
    return [r.get("movie_id") for r in res["recs"]]


def score_sql_question(q: dict, res: dict, gold: dict) -> dict:
    out: dict = {}
    if q["compare"] == "ordered":
        g = gold["ids"]
        k = len(g)
        rec = recommended_ids(res)[:k]
        out["exact"] = float(rec == g)
        out["precision_at_k"] = len({x for x in rec if x is not None} & set(g)) / k
        if res.get("sql_columns") and "movie_id" in res["sql_columns"]:
            i = res["sql_columns"].index("movie_id")
            sys_ids = [r[i] for r in res["sql_rows"]][:k]
            out["exec_match"] = float(sys_ids == g)
    else:
        gv = float(gold["value"])
        candidates = []
        if res.get("numeric_answer") is not None:
            candidates.append(float(res["numeric_answer"]))
        candidates += numbers_in_text(res.get("text", ""))
        out["exact"] = float(any(close(c, gv) for c in candidates))
        out["precision_at_k"] = out["exact"]
        if res.get("sql_rows"):
            first = [v for v in res["sql_rows"][0] if isinstance(v, (int, float))]
            out["exec_match"] = float(any(close(float(v), gv) for v in first))
    return out


def score_judged(q: dict, res: dict) -> dict:
    """Usa as notas do juiz já gravadas em res['relevance']."""
    rel = res.get("relevance") or []
    padded = (rel + [0] * K)[:K]
    out = {
        "relevance_mean": float(np.mean(rel)) / 2 if rel else 0.0,
        "precision_at_5": sum(1 for r in padded if r >= 1) / K,
        "n_recs": len(res["recs"]),
    }
    if q["category"] == "mixed":
        f = Filters.empty().model_copy(update=q["constraints"])
        sat = [bool(r.get("record")) and db.satisfies(r["record"], f) for r in res["recs"]]
        sat_p = (sat + [False] * K)[:K]
        out["constraint_rate"] = float(np.mean(sat)) if sat else 0.0
        out["mixed_hit_at_5"] = sum(1 for s, r in zip(sat_p, padded) if s and r >= 1) / K
    return out


def hallucination_counts(res: dict) -> dict:
    """Itens recomendados com algum dado fabricado.

    - Só LLM: título inexistente no acervo, ano errado, gênero errado ou nota com erro > 0,3.
    - CineGraph: cartões vêm do banco; conta-se só o ID inventado que o modelo tentou citar
      (descartado antes de chegar ao usuário).
    """
    if res["config"] == "llm_only":
        n = len(res["recs"])
        bad = sum(1 for r in res["recs"] if not all(r.get("checks", {}).values()) or not r.get("checks"))
        nonexistent = sum(1 for r in res["recs"] if not r.get("checks", {}).get("exists"))
        return {"items": n, "hallucinated": bad, "nonexistent": nonexistent, "shown_hallucinated": bad}
    dropped = len(res.get("dropped_ids") or [])
    return {"items": len(res["recs"]) + dropped, "hallucinated": dropped, "nonexistent": dropped,
            "shown_hallucinated": 0}


# ---------------------------------------------------------------- agregação

def aggregate(questions: dict[str, dict], results: list[dict]) -> dict:
    by_cfg: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cfg[r["config"]].append(r)

    metrics: dict = {"configs": {}, "routing": None}
    for cfg, rs in by_cfg.items():
        m: dict = {"n": len(rs)}
        for cat, keys in (("sql", ["exact", "precision_at_k", "exec_match"]),
                          ("semantic", ["relevance_mean", "precision_at_5"]),
                          ("mixed", ["relevance_mean", "precision_at_5", "constraint_rate", "mixed_hit_at_5"])):
            cat_rs = [r for r in rs if questions[r["qid"]]["category"] == cat]
            if not cat_rs:
                continue
            m[cat] = {}
            for k in keys:
                vals = [r["scores"][k] for r in cat_rs if k in r.get("scores", {})]
                if vals:
                    mean, lo, hi = bootstrap_ci(vals)
                    m[cat][k] = {"mean": mean, "ci95": [lo, hi], "n": len(vals)}
        h = Counter()
        for r in rs:
            h.update(r.get("hallucination", {}))
        m["hallucination"] = {
            "items": h["items"], "hallucinated": h["hallucinated"], "nonexistent": h["nonexistent"],
            "rate": h["hallucinated"] / h["items"] if h["items"] else 0.0,
            "nonexistent_rate": h["nonexistent"] / h["items"] if h["items"] else 0.0,
            "shown_rate": h["shown_hallucinated"] / h["items"] if h["items"] else 0.0,
        }
        lat = [r["latency_s"] for r in rs if r.get("latency_s") is not None]
        cost = [r["cost_usd"] for r in rs]
        m["latency_s"] = {"p50": float(np.percentile(lat, 50)) if lat else None,
                          "p95": float(np.percentile(lat, 95)) if lat else None}
        m["cost_usd"] = {"total": float(sum(cost)), "per_question": float(np.mean(cost)) if cost else 0.0}
        m["errors"] = sum(1 for r in rs if r.get("error"))
        metrics["configs"][cfg] = m

    routed = by_cfg.get("routed", [])
    if routed:
        labels = ["sql", "vector", "hybrid", "outro"]
        confusion = {e: {p: 0 for p in labels} for e in labels}
        per_cat = defaultdict(list)
        for r in routed:
            cat = questions[r["qid"]]["category"]
            exp, pred = EXPECTED_ROUTE[cat], r.get("route") or "outro"
            confusion[exp][pred if pred in labels else "outro"] += 1
            per_cat[cat].append(float(exp == pred))
        allv = [v for vs in per_cat.values() for v in vs]
        mean, lo, hi = bootstrap_ci(allv)
        metrics["routing"] = {
            "accuracy": {"mean": mean, "ci95": [lo, hi], "n": len(allv)},
            "per_category": {c: float(np.mean(v)) for c, v in per_cat.items()},
            "confusion": confusion,
        }
    return metrics
