"""Testes estatísticos e métricas por classe, no espírito da aula de análise de desempenho (PTC3467).

- Roteador como classificador multiclasse: acurácia, taxa de erro e, para cada rota tratada como
  positiva contra as demais, precisão, revocação e especificidade.
- Comparação entre configurações sobre as mesmas perguntas, com testes pareados:
  McNemar exato (métricas binárias), Wilcoxon dos postos sinalizados (precisão@5) e
  Fisher exato (contagens de itens com dado errado). Correção de Holm para comparações múltiplas.

Só numpy e math: os valores batem com scipy.stats (wilcoxon com aproximação normal e correção
de empates, que é o que o scipy usa quando há empates; binomtest; fisher_exact).
"""

from __future__ import annotations

import math

import numpy as np

ALPHA = 0.05
EXPECTED_ROUTE = {"sql": "sql", "semantic": "vector", "mixed": "hybrid", "outro": "outro"}


def per_class_metrics(confusion: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    """Linhas = classe verdadeira, colunas = classe predita (convenção da aula)."""
    labels = list(confusion)
    n = sum(sum(row.values()) for row in confusion.values())
    out: dict[str, dict[str, float]] = {}
    for c in labels:
        vp = confusion[c][c]
        fn = sum(confusion[c].values()) - vp
        fp = sum(confusion[e][c] for e in labels) - vp
        vn = n - vp - fn - fp
        out[c] = {
            "vp": vp, "fn": fn, "fp": fp, "vn": vn,
            "precision": vp / (vp + fp) if vp + fp else float("nan"),
            "recall": vp / (vp + fn) if vp + fn else float("nan"),
            "specificity": vn / (vn + fp) if vn + fp else float("nan"),
        }
    keys = ("precision", "recall", "specificity")
    out["macro"] = {k: float(np.nanmean([out[c][k] for c in labels])) for k in keys}
    return out


def mcnemar_exact(a: np.ndarray, b: np.ndarray) -> dict:
    """Teste de McNemar exato (binomial) para pares de resultados 0/1."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    b01 = int(((a == 1) & (b == 0)).sum())  # só `a` acertou
    b10 = int(((a == 0) & (b == 1)).sum())  # só `b` acertou
    k = b01 + b10
    if k == 0:
        return {"test": "McNemar", "a_only": b01, "b_only": b10, "p": 1.0}
    tail = sum(math.comb(k, i) for i in range(min(b01, b10) + 1)) / 2**k
    return {"test": "McNemar", "a_only": b01, "b_only": b10, "p": min(1.0, 2 * tail)}


def _rank_abs(x: np.ndarray) -> np.ndarray:
    """Postos médios (empates recebem a média dos postos)."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def wilcoxon_signed_rank(a: np.ndarray, b: np.ndarray) -> dict:
    """Wilcoxon pareado, bilateral, descartando diferenças nulas; aproximação normal com correção de empates."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[d != 0]
    n = len(d)
    if n == 0:
        return {"test": "Wilcoxon", "n_nonzero": 0, "p": 1.0}
    r = _rank_abs(np.abs(d))
    r_plus = float(r[d > 0].sum())
    mean = n * (n + 1) / 4
    _, counts = np.unique(np.abs(d), return_counts=True)
    var = n * (n + 1) * (2 * n + 1) / 24 - float((counts**3 - counts).sum()) / 48
    if var <= 0:
        return {"test": "Wilcoxon", "n_nonzero": n, "p": 1.0}
    z = (r_plus - mean) / math.sqrt(var)
    return {"test": "Wilcoxon", "n_nonzero": n, "z": z, "p": min(1.0, math.erfc(abs(z) / math.sqrt(2)))}


def fisher_exact(a_hits: int, a_n: int, b_hits: int, b_n: int) -> dict:
    """Fisher exato bilateral para a tabela [[a_hits, a_n - a_hits], [b_hits, b_n - b_hits]]."""
    col = a_hits + b_hits
    total = a_n + b_n

    def logp(x: int) -> float:
        return (math.lgamma(a_n + 1) - math.lgamma(x + 1) - math.lgamma(a_n - x + 1)
                + math.lgamma(b_n + 1) - math.lgamma(col - x + 1) - math.lgamma(b_n - col + x + 1)
                - math.lgamma(total + 1) + math.lgamma(col + 1) + math.lgamma(total - col + 1))

    lo, hi = max(0, col - b_n), min(a_n, col)
    obs = logp(a_hits)
    p = sum(math.exp(logp(x)) for x in range(lo, hi + 1) if logp(x) <= obs + 1e-7)
    return {"test": "Fisher", "p": min(1.0, p)}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """p-valores ajustados por Holm–Bonferroni."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m, running, out = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def paired(results: list[dict], questions: dict[str, dict], cfg: str, cat: str, key: str) -> dict[str, float]:
    return {r["qid"]: float(r["scores"][key]) for r in results
            if r["config"] == cfg and questions[r["qid"]]["category"] == cat
            and r.get("scores", {}).get(key) is not None}


def _pair(results, questions, a_cfg, b_cfg, cat, key):
    a, b = paired(results, questions, a_cfg, cat, key), paired(results, questions, b_cfg, cat, key)
    qs = sorted(set(a) & set(b))
    return np.array([a[q] for q in qs]), np.array([b[q] for q in qs])


# (hipótese, categoria, métrica, configuração da hipótese, concorrentes, teste)
HYPOTHESES = [
    ("H1", "sql", "exact", "sql", ["vector", "hybrid"], "mcnemar"),
    ("H2", "semantic", "precision_at_5", "vector", ["sql"], "wilcoxon"),
    ("H3", "mixed", "mixed_hit_at_5", "hybrid", ["sql", "vector"], "wilcoxon"),
]


def hypothesis_tests(results: list[dict], questions: dict[str, dict]) -> dict:
    """Testa H1–H4 e compara o roteado com o melhor mecanismo forçado de cada categoria."""
    out: dict = {"alpha": ALPHA, "hypotheses": {}, "routed_vs_best": {}}
    raw_p: dict[str, float] = {}
    for h, cat, key, cfg, rivals, kind in HYPOTHESES:
        comps = []
        for rival in rivals:
            a, b = _pair(results, questions, cfg, rival, cat, key)
            if len(a) == 0:
                continue
            t = mcnemar_exact(a, b) if kind == "mcnemar" else wilcoxon_signed_rank(a, b)
            t.update(vs=rival, n=len(a), mean_a=float(a.mean()), mean_b=float(b.mean()))
            comps.append(t)
            raw_p[f"{h}:{rival}"] = t["p"]
        out["hypotheses"][h] = {"category": cat, "metric": key, "config": cfg, "comparisons": comps}

    def items(cfg):
        rs = [r for r in results if r["config"] == cfg and questions[r["qid"]]["category"] != "outro"]
        return (sum(r["hallucination"]["hallucinated"] for r in rs), sum(r["hallucination"]["items"] for r in rs),
                sum(1 for r in rs if r["hallucination"]["hallucinated"] > 0), len(rs))

    cfgs = {r["config"] for r in results}
    if {"routed", "llm_only"} <= cfgs:
        ha, na, qa, nqa = items("routed")
        hb, nb, qb, nqb = items("llm_only")
        t = fisher_exact(ha, na, hb, nb)
        t.update(vs="llm_only", rate_a=ha / na if na else None, rate_b=hb / nb if nb else None,
                 items_a=[ha, na], items_b=[hb, nb], questions_a=[qa, nqa], questions_b=[qb, nqb])
        out["hypotheses"]["H4"] = {"category": "todas", "metric": "hallucination", "config": "routed",
                                   "comparisons": [t]}
        raw_p["H4:llm_only"] = t["p"]

    adj = holm(raw_p)
    for h, v in out["hypotheses"].items():
        for t in v["comparisons"]:
            t["p_holm"] = adj[f"{h}:{t['vs']}"]
        ok = bool(v["comparisons"]) and all(
            t["p_holm"] < ALPHA and (t.get("mean_a", 0) > t.get("mean_b", 0) if "mean_a" in t
                                     else (t["rate_a"] or 0) < (t["rate_b"] or 0))
            for t in v["comparisons"])
        v["verdict"] = "confirmada" if ok else "não confirmada (sem significância a 5%)"

    if "routed" in cfgs:
        for cat, key in (("sql", "exact"), ("semantic", "precision_at_5"), ("mixed", "mixed_hit_at_5")):
            means = {c: np.mean(list(paired(results, questions, c, cat, key).values()) or [np.nan])
                     for c in ("sql", "vector", "hybrid") if c in cfgs}
            if not means:
                continue
            best = max(means, key=lambda c: means[c])
            a, b = _pair(results, questions, "routed", best, cat, key)
            t = mcnemar_exact(a, b) if key == "exact" else wilcoxon_signed_rank(a, b)
            t.update(best=best, mean_routed=float(a.mean()), mean_best=float(b.mean()))
            out["routed_vs_best"][cat] = t
    return out


def mean_sd(values: list[float]) -> tuple[float, float]:
    v = np.asarray(values, float)
    return float(v.mean()), float(v.std(ddof=1)) if len(v) > 1 else 0.0
