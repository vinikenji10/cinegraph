"""Relatório da avaliação: reports/summary.md + gráficos PNG.

Cada execução também fica guardada em reports/runs/<data>/ (resultados brutos e métricas).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from cinegraph.evaluation import stats

CONFIG_LABEL = {"routed": "CineGraph (roteado)", "sql": "Só SQL", "vector": "Só vetorial",
                "hybrid": "Só híbrido", "llm_only": "Só LLM"}
CONFIG_SHORT = {"routed": "Roteado", "sql": "SQL", "vector": "Vetorial", "hybrid": "Híbrido", "llm_only": "Só LLM"}
# Paleta categórica validada (dataviz/validate_palette.js, modo claro): ordem fixa por configuração.
COLORS = {"routed": "#2a78d6", "sql": "#eb6834", "vector": "#1baf7a", "hybrid": "#eda100", "llm_only": "#e87ba4"}
TEXT, TEXT2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
ORDER = ["routed", "sql", "vector", "hybrid", "llm_only"]


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def _metric(m: dict, cfg: str, cat: str, key: str) -> dict | None:
    return m["configs"].get(cfg, {}).get(cat, {}).get(key)


def _cell(v: dict | None) -> str:
    if not v:
        return "—"
    lo, hi = v["ci95"]
    return f"{100 * v['mean']:.0f}% ({100 * lo:.0f}–{100 * hi:.0f})"


def _p(p: float) -> str:
    if p < 0.001:
        return "p < 0,001"
    return f"p = {p:.3f}".replace(".", ",")


def _tests_cell(h: dict) -> str:
    parts = []
    for t in h["comparisons"]:
        vs = CONFIG_SHORT.get(t["vs"], t["vs"])
        parts.append(f"{t['test']} × {vs}: {_p(t['p'])} (Holm {_p(t['p_holm']).removeprefix('p ')})")
    return "; ".join(parts) or "—"


def _charts(metrics: dict, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT2,
        "xtick.color": TEXT2, "ytick.color": TEXT2, "axes.titlecolor": TEXT, "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    })
    files = []
    cfgs = [c for c in ORDER if c in metrics["configs"]]

    # 1) Hipóteses H1–H3: métrica principal de cada categoria, por configuração
    panels = [("sql", "exact", "Objetivas (H1)\nacerto exato"),
              ("semantic", "precision_at_5", "Semânticas (H2)\nprecisão@5 (juiz)"),
              ("mixed", "mixed_hit_at_5", "Mistas (H3)\nrelevante e dentro dos filtros @5")]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.9), sharey=True)
    for ax, (cat, key, title) in zip(axes, panels):
        vals = [_metric(metrics, c, cat, key) for c in cfgs]
        xs = range(len(cfgs))
        means = [v["mean"] if v else 0 for v in vals]
        err = [[(v["mean"] - v["ci95"][0]) if v else 0 for v in vals],
               [(v["ci95"][1] - v["mean"]) if v else 0 for v in vals]]
        ax.bar(xs, means, width=0.62, color=[COLORS[c] for c in cfgs], edgecolor=SURFACE, linewidth=2, zorder=2)
        ax.errorbar(xs, means, yerr=err, fmt="none", ecolor=TEXT2, elinewidth=1, capsize=3, zorder=3)
        for x, v in zip(xs, vals):
            if v:
                ax.text(x, v["ci95"][1] + 0.03, f"{100 * v['mean']:.0f}%", ha="center", va="bottom", color=TEXT, fontsize=9)
        ax.set_xticks(list(xs), [CONFIG_SHORT[c] for c in cfgs], rotation=0, fontsize=8.5)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_ylim(0, 1.15)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
    axes[0].set_yticks([0, .25, .5, .75, 1], ["0%", "25%", "50%", "75%", "100%"])
    fig.text(0.01, 0.01, "Barras de erro: intervalo de confiança de 95% (bootstrap por pergunta).", color=TEXT2, fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out / "hipoteses.png", dpi=160)
    plt.close(fig)
    files.append("hipoteses.png")

    # 2) H4: taxa de alucinação por configuração
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    rates = [metrics["configs"][c]["hallucination"]["rate"] for c in cfgs]
    ax.barh(range(len(cfgs)), rates, height=0.6, color=[COLORS[c] for c in cfgs], edgecolor=SURFACE, linewidth=2, zorder=2)
    for y, (c, r) in enumerate(zip(cfgs, rates)):
        h = metrics["configs"][c]["hallucination"]
        ax.text(r + 0.01, y, f"{100 * r:.1f}%  ({h['hallucinated']}/{h['items']})", va="center", color=TEXT, fontsize=9)
    ax.set_yticks(range(len(cfgs)), [CONFIG_LABEL[c] for c in cfgs])
    ax.invert_yaxis()
    ax.set_xlim(0, max(0.1, max(rates) * 1.45))
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    ax.set_title("Itens recomendados com dado fabricado (H4)", loc="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "alucinacao.png", dpi=160)
    plt.close(fig)
    files.append("alucinacao.png")

    # 3) Matriz de confusão do roteamento (escala sequencial de um matiz)
    if metrics.get("routing"):
        import numpy as np
        from matplotlib.colors import LinearSegmentedColormap

        labels = ["sql", "vector", "hybrid", "outro"]
        names = ["SQL", "Vetorial", "Híbrido", "Outro"]
        conf = metrics["routing"]["confusion"]
        mat = np.array([[conf[e][p] for p in labels] for e in labels], dtype=float)
        cmap = LinearSegmentedColormap.from_list("blue", ["#f3f7fd", "#9ec5f4", "#3987e5", "#184f95"])
        fig, ax = plt.subplots(figsize=(4.6, 3.9))
        ax.imshow(mat, cmap=cmap, vmin=0, vmax=max(1, mat.max()))
        for i in range(4):
            for j in range(4):
                v = int(mat[i, j])
                ax.text(j, i, str(v), ha="center", va="center", fontsize=10,
                        color="#ffffff" if mat[i, j] > 0.55 * mat.max() else TEXT)
        ax.set_xticks(range(4), names)
        ax.set_yticks(range(4), names)
        ax.set_xlabel("Rota escolhida")
        ax.set_ylabel("Rota esperada")
        acc = metrics["routing"]["accuracy"]["mean"]
        ax.set_title(f"Roteamento: acurácia {100 * acc:.0f}%", loc="left", fontsize=10)
        for s in ax.spines.values():
            s.set_visible(False)
        fig.tight_layout()
        fig.savefig(out / "roteamento.png", dpi=160)
        plt.close(fig)
        files.append("roteamento.png")
    return files


def regenerate(run_dir: Path) -> Path:
    """Refaz o relatório de uma execução já gravada (results.jsonl + metrics.json), sem chamar a API."""
    from cinegraph.evaluation.run import load_questions

    results = [json.loads(line) for line in (run_dir / "results.jsonl").open()]
    metrics = json.loads((run_dir / "metrics.json").read_text())
    qmap = {q["id"]: q for q in load_questions()}
    return write_report(metrics, results, qmap, run_dir)


def write_report(metrics: dict, results: list[dict], qmap: dict, run_dir: Path) -> Path:
    from cinegraph.evaluation.run import REPORTS_DIR

    figs = _charts(metrics, run_dir)
    m, meta = metrics, metrics["meta"]
    cfgs = [c for c in ORDER if c in m["configs"]]

    def mean(cfg, cat, key):
        v = _metric(m, cfg, cat, key)
        return v["mean"] if v else None

    L: list[str] = []
    L.append("# Avaliação do CineGraph\n")
    L.append(f"Execução `{meta['run']}` · {meta['n_questions']} perguntas · modelo `{meta['llm_model']}` · "
             f"embeddings `{meta['embed_model'].split('/')[-1]}` · prompts v{meta['prompt_version']} · "
             f"custo total US$ {meta['total_cost_usd']:.2f}\n")

    tests = stats.hypothesis_tests(results, qmap)
    (run_dir / "tests.json").write_text(json.dumps(tests, indent=2, ensure_ascii=False))
    H = tests["hypotheses"]

    L.append("## Hipóteses do projeto\n")
    L.append("Testes pareados por pergunta (as configurações respondem às mesmas perguntas), α = 5%, "
             "p-valores também ajustados por Holm para as comparações múltiplas.\n")
    L.append("| Hipótese | Métrica | Resultado | Teste | Veredito |\n|---|---|---|---|---|")
    h1 = (mean("sql", "sql", "exact"), [mean("vector", "sql", "exact"), mean("hybrid", "sql", "exact")])
    h2 = (mean("vector", "semantic", "precision_at_5"), [mean("sql", "semantic", "precision_at_5")])
    h3 = (mean("hybrid", "mixed", "mixed_hit_at_5"), [mean("sql", "mixed", "mixed_hit_at_5"), mean("vector", "mixed", "mixed_hit_at_5")])
    hr = {c: m["configs"][c]["hallucination"]["rate"] for c in cfgs}

    def row(h, label, metric, result):
        if h in H:
            L.append(f"| {label} | {metric} | {result} | {_tests_cell(H[h])} | {H[h]['verdict']} |")

    row("H1", "H1. SQL é mais preciso em filtros, contagens e rankings", "acerto exato nas objetivas",
        f"SQL {_pct(h1[0])} · vetorial {_pct(h1[1][0])} · híbrido {_pct(h1[1][1])}")
    row("H2", "H2. Busca vetorial é mais relevante em tema e atmosfera", "precisão@5 nas semânticas",
        f"vetorial {_pct(h2[0])} · SQL {_pct(h2[1][0])}")
    row("H3", "H3. Híbrido é melhor em perguntas mistas", "relevante e dentro dos filtros @5",
        f"híbrido {_pct(h3[0])} · SQL {_pct(h3[1][0])} · vetorial {_pct(h3[1][1])}")
    if "routed" in hr and "llm_only" in hr:
        row("H4", "H4. Roteamento inventa menos que um chatbot só com LLM", "itens com dado fabricado",
            f"roteado {100 * hr['routed']:.1f}% · só LLM {100 * hr['llm_only']:.1f}%")
    L.append("")
    if "H4" in H:
        t = H["H4"]["comparisons"][0]
        L.append(f"Por pergunta: {t['questions_b'][0]} das {t['questions_b'][1]} respostas do só LLM têm algum item "
                 f"com dado errado; no roteado, {t['questions_a'][0]} de {t['questions_a'][1]}.\n")
    if tests["routed_vs_best"]:
        L.append("**Roteado × melhor mecanismo forçado de cada categoria:** " + "; ".join(
            f"{cat} ({CONFIG_SHORT[t['best']]}):{_pct(t['mean_routed'])} × {_pct(t['mean_best'])}, "
            f"{t['test']} {_p(t['p'])}" for cat, t in tests["routed_vs_best"].items()) + ".\n")
    if "hipoteses.png" in figs:
        L.append("![Hipóteses H1–H3](hipoteses.png)\n")
    if "alucinacao.png" in figs:
        L.append("![Alucinação](alucinacao.png)\n")

    if m.get("routing"):
        r = m["routing"]
        lo, hi = r["accuracy"]["ci95"]
        L.append("## Roteamento\n")
        L.append(f"Acurácia geral: **{100 * r['accuracy']['mean']:.0f}%** (IC95% {100 * lo:.0f}–{100 * hi:.0f}%, "
                 f"n={r['accuracy']['n']}). Por categoria: " +
                 ", ".join(f"{c} {100 * v:.0f}%" for c, v in r["per_category"].items()) + ".\n")
        L.append("| esperada \\ escolhida | sql | vector | hybrid | outro |\n|---|---|---|---|---|")
        for e, crow in r["confusion"].items():
            L.append(f"| {e} | " + " | ".join(str(crow[p]) for p in ("sql", "vector", "hybrid", "outro")) + " |")
        L.append("")
        pc = stats.per_class_metrics(r["confusion"])
        L.append(f"Taxa de erro: {100 * (1 - r['accuracy']['mean']):.1f}%. Por rota, tratada como positiva contra as "
                 "demais:\n")
        L.append("| rota | precisão | revocação | especificidade |\n|---|---|---|---|")
        for c, v in pc.items():
            name = "média (macro)" if c == "macro" else c
            L.append(f"| {name} | " + " | ".join(f"{100 * v[k]:.1f}%" for k in ("precision", "recall", "specificity"))
                     + " |")
        L.append("")
        if "roteamento.png" in figs:
            L.append("![Matriz de confusão](roteamento.png)\n")

    L.append("## Resultados por categoria\n")
    L.append("Valores médios com intervalo de confiança de 95% (bootstrap sobre as perguntas).\n")
    L.append("**Objetivas (30 perguntas com SQL de referência)**\n")
    L.append("| Configuração | Acerto exato | Precisão@k | SQL igual ao gabarito |\n|---|---|---|---|")
    for c in cfgs:
        L.append(f"| {CONFIG_LABEL[c]} | {_cell(_metric(m, c, 'sql', 'exact'))} | "
                 f"{_cell(_metric(m, c, 'sql', 'precision_at_k'))} | {_cell(_metric(m, c, 'sql', 'exec_match'))} |")
    L.append("\n**Semânticas (relevância dada pelo juiz)**\n")
    L.append("| Configuração | Precisão@5 | Relevância média |\n|---|---|---|")
    for c in cfgs:
        L.append(f"| {CONFIG_LABEL[c]} | {_cell(_metric(m, c, 'semantic', 'precision_at_5'))} | "
                 f"{_cell(_metric(m, c, 'semantic', 'relevance_mean'))} |")
    L.append("\n**Mistas (relevância + filtros conferidos no banco)**\n")
    L.append("| Configuração | Relevante e nos filtros @5 | Respeita filtros | Precisão@5 |\n|---|---|---|---|")
    for c in cfgs:
        L.append(f"| {CONFIG_LABEL[c]} | {_cell(_metric(m, c, 'mixed', 'mixed_hit_at_5'))} | "
                 f"{_cell(_metric(m, c, 'mixed', 'constraint_rate'))} | {_cell(_metric(m, c, 'mixed', 'precision_at_5'))} |")

    L.append("\n**Média ± desvio-padrão por pergunta**\n")
    L.append("| Configuração | Objetivas · acerto exato | Semânticas · precisão@5 | Mistas · relevante e nos filtros @5 |\n"
             "|---|---|---|---|")
    for c in cfgs:
        cells = []
        for cat, key in (("sql", "exact"), ("semantic", "precision_at_5"), ("mixed", "mixed_hit_at_5")):
            vals = list(stats.paired(results, qmap, c, cat, key).values())
            if vals:
                mu, sd = stats.mean_sd(vals)
                cells.append(f"{100 * mu:.0f} ± {100 * sd:.0f}%")
            else:
                cells.append("—")
        L.append(f"| {CONFIG_LABEL[c]} | " + " | ".join(cells) + " |")

    L.append("\n## Alucinação, latência e custo\n")
    L.append("| Configuração | Dado fabricado | Título inexistente | Chega ao usuário | Latência p50 | p95 | US$/pergunta | Erros |\n"
             "|---|---|---|---|---|---|---|---|")
    for c in cfgs:
        x = m["configs"][c]
        h = x["hallucination"]
        L.append(f"| {CONFIG_LABEL[c]} | {100 * h['rate']:.1f}% ({h['hallucinated']}/{h['items']}) | "
                 f"{100 * h['nonexistent_rate']:.1f}% | {100 * h['shown_rate']:.1f}% | "
                 f"{x['latency_s']['p50']:.1f} s | {x['latency_s']['p95']:.1f} s | "
                 f"{x['cost_usd']['per_question']:.4f} | {x['errors']} |")
    L.append("\nNo CineGraph, título, ano, gêneros e nota exibidos vêm do banco; o único dado que o modelo pode "
             "inventar é um ID fora dos candidatos, e esse ID é descartado antes de chegar ao usuário "
             "(coluna \"Chega ao usuário\").\n")

    misses = [r for r in results if r["config"] == "routed" and qmap[r["qid"]]["category"] == "sql"
              and r["scores"].get("exact") == 0.0]
    if misses:
        L.append("## Perguntas objetivas que o sistema roteado errou\n")
        L.append("| id | pergunta | rota | SQL gerado |\n|---|---|---|---|")
        for r in sorted(misses, key=lambda x: x["qid"])[:12]:
            sql = (r["sql"] or r.get("sql_error") or "—").replace("|", "\\|")
            L.append(f"| {r['qid']} | {qmap[r['qid']]['question']} | {r['route']} | `{sql[:160]}` |")
        L.append("")

    L.append("## Método e limitações\n")
    L.append("- A rota e os filtros são extraídos uma vez por pergunta e reaproveitados nas configurações forçadas, "
             "que só trocam o mecanismo de recuperação.")
    L.append("- A busca vetorial da avaliação usa a cópia local dos vetores (busca exata por cosseno), para não gastar a "
             "cota diária de leituras do Firestore. O site usa o Firestore com os mesmos vetores.")
    L.append("- A relevância é dada por um juiz LLM (o mesmo modelo), que pode favorecer filmes famosos e as "
             "próprias escolhas. Vale revisar por amostragem.")
    L.append("- Com 30 perguntas por categoria, o poder estatístico é baixo: diferenças de cerca de 10 pontos podem "
             "não ser significativas a 5%.")
    L.append("- Não há treino (o LLM é usado sem ajuste fino), mas algumas perguntas do conjunto serviram para ajustar "
             "prompts e o índice durante o desenvolvimento; um conjunto de teste novo daria uma estimativa sem esse viés.")
    L.append("- O baseline \"só LLM\" é conferido contra o MovieLens: título, ano (±1), gêneros e nota média (±0,3).\n")

    text = "\n".join(L)
    (run_dir / "summary.md").write_text(text)
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "summary.md").write_text(text)
    for f in figs:
        shutil.copy(run_dir / f, REPORTS_DIR / f)
    return REPORTS_DIR / "summary.md"
