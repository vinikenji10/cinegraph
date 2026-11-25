"""Gera as figuras do relatório.

Lê o banco SQLite produzido por `uv run cinegraph build-db` e as métricas da
execução avaliada (`reports/runs/<execução>/metrics.json`) e grava PDFs vetoriais
nesta pasta. Rode a partir da raiz do projeto:

    uv run python relatorio/figuras/gerar_figuras.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "processed" / "cinegraph.db"
OUT = Path(__file__).resolve().parent
RUN = ROOT / "reports" / "runs" / "20261117-173343"

# Uma cor fixa por configuração (paleta categórica, posições 1 a 5).
CONFIGS = [
    ("routed", "Roteado", "#2a78d6"),
    ("sql", "Só SQL", "#eb6834"),
    ("vector", "Só vetorial", "#1baf7a"),
    ("hybrid", "Só híbrido", "#eda100"),
    ("llm_only", "Só LLM", "#e87ba4"),
]

BLUE = "#2a78d6"      # série única (paleta categórica, posição 1)
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.5,
    "axes.edgecolor": INK_2,
    "axes.labelcolor": INK_2,
    "axes.titlesize": 9,
    "axes.titleweight": "bold",
    "axes.titlecolor": INK,
    "axes.titlelocation": "left",
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 150,
    "savefig.bbox": "tight",
    "pdf.fonttype": 42,
})


def _fmt_int(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", ".")


def _ygrid(ax) -> None:
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def fig_avaliacoes(con: sqlite3.Connection) -> None:
    dist = con.execute("SELECT rating, COUNT(*) FROM ratings GROUP BY rating ORDER BY rating").fetchall()
    n_ratings = [r[0] for r in con.execute("SELECT n_ratings FROM movies")]
    total = len(n_ratings)
    bins = [("0", 0, 0), ("1", 1, 1), ("2–4", 2, 4), ("5–9", 5, 9), ("10–49", 10, 49),
            ("50–99", 50, 99), ("100+", 100, 10**9)]
    shares = [sum(lo <= n <= hi for n in n_ratings) / total for _, lo, hi in bins]

    fig, (a, b) = plt.subplots(1, 2, figsize=(6.6, 2.5), gridspec_kw={"wspace": 0.38})

    xs = [r[0] for r in dist]
    ys = [r[1] / 1000 for r in dist]
    a.bar(xs, ys, width=0.38, color=BLUE, edgecolor="white", linewidth=0.8)
    a.set_title("(a) Distribuição das notas")
    a.set_xlabel("nota")
    a.set_ylabel("avaliações (milhares)")
    a.set_xticks(xs)
    a.set_xticklabels([f"{x:g}".replace(".", ",") for x in xs])
    _ygrid(a)

    labels = [lab for lab, _, _ in bins]
    b.bar(labels, [s * 100 for s in shares], width=0.62, color=BLUE, edgecolor="white", linewidth=0.8)
    for i, s in enumerate(shares):
        txt = f"{s * 100:.1f}%" if s < 0.1 else f"{s * 100:.0f}%"
        b.text(i, s * 100 + 1.2, txt.replace(".", ","), ha="center", va="bottom",
               fontsize=7.5, color=INK)
    b.set_title("(b) Avaliações por filme")
    b.set_xlabel("número de avaliações do filme")
    b.tick_params(axis="x", labelsize=7.4)
    plt.setp(b.get_xticklabels(), rotation=35, ha="right", rotation_mode="anchor")
    b.set_ylabel("% dos filmes")
    b.set_ylim(0, max(shares) * 100 + 9)
    _ygrid(b)

    fig.savefig(OUT / "fig_avaliacoes.pdf")
    plt.close(fig)


def fig_hipoteses() -> None:
    metrics = json.loads((RUN / "metrics.json").read_text())["configs"]
    panels = [
        ("sql", "exact", "(a) Objetivas (H1)", "acerto exato"),
        ("semantic", "precision_at_5", "(b) Semânticas (H2)", "precisão@5"),
        ("mixed", "mixed_hit_at_5", "(c) Mistas (H3)", "relevante e nos filtros@5"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(6.6, 2.7), sharey=True, gridspec_kw={"wspace": 0.12})
    for ax, (cat, key, title, ylabel) in zip(axes, panels):
        for i, (cfg, _, color) in enumerate(CONFIGS):
            m = metrics[cfg][cat][key]
            mean, (lo, hi) = m["mean"] * 100, (m["ci95"][0] * 100, m["ci95"][1] * 100)
            ax.bar(i, mean, width=0.7, color=color, edgecolor="white", linewidth=0.8)
            ax.errorbar(i, mean, yerr=[[mean - lo], [hi - mean]], color=INK_2, capsize=2.2,
                        linewidth=0.8, capthick=0.8)
            ax.text(i, hi + 2.5, f"{mean:.0f}%", ha="center", va="bottom", fontsize=7, color=INK)
        ax.set_title(title)
        ax.set_xlabel(ylabel, fontsize=7.6)
        ax.set_xticks(range(len(CONFIGS)))
        ax.set_xticklabels([lab for _, lab, _ in CONFIGS], rotation=40, ha="right",
                           rotation_mode="anchor", fontsize=7.2)
        ax.set_ylim(0, 115)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.tick_params(axis="y", labelsize=7.4)
        _ygrid(ax)
    axes[0].set_ylabel("% (média por pergunta)")
    fig.savefig(OUT / "fig_hipoteses.pdf")
    plt.close(fig)


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        fig_avaliacoes(con)
    finally:
        con.close()
    fig_hipoteses()
    print("Figuras gravadas em", OUT)


if __name__ == "__main__":
    main()
