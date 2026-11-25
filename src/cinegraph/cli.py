"""Linha de comando do pipeline: `uv run cinegraph <etapa>`.

Etapas, na ordem de reprodução do DMP:
    download      baixa e congela o MovieLens (data/raw + MANIFEST.json)
    build-db      monta o SQLite em data/processed/cinegraph.db
    build-index   gera perfis e embeddings (data/processed)
    upload-index  envia os vetores para o Firestore
    ask           faz uma pergunta ao chatbot pelo terminal
    eval          roda a avaliação (ver eval/README.md)
    report        refaz o relatório e os testes estatísticos de uma execução gravada, sem chamar a API
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cinegraph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("download")
    p.add_argument("--force", action="store_true")
    sub.add_parser("build-db")
    sub.add_parser("build-index")
    p = sub.add_parser("upload-index")
    p.add_argument("--project", default=None)
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--route", choices=["sql", "vector", "hybrid", "llm_only"], default=None)
    p = sub.add_parser("eval")
    p.add_argument("--limit", type=int, default=None, help="usa só as N primeiras perguntas")
    p.add_argument("--configs", default="routed,sql,vector,hybrid,llm_only")
    p.add_argument("--ids", default=None, help="ids de perguntas separados por vírgula")
    p.add_argument("--no-judge", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("run_dir", nargs="?", default=None, help="reports/runs/<data>; padrão: a execução mais recente")
    args = parser.parse_args(argv)

    if args.cmd == "download":
        from cinegraph.data.download import download

        print(json.dumps(download(force=args.force), indent=2, ensure_ascii=False))
    elif args.cmd == "build-db":
        from cinegraph.data.prepare import build_database

        print(json.dumps(build_database(), indent=2, ensure_ascii=False))
    elif args.cmd == "build-index":
        from cinegraph.retrieval.index import build_index

        print(json.dumps(build_index(), indent=2, ensure_ascii=False))
    elif args.cmd == "upload-index":
        from cinegraph.retrieval.vectorstore import upload_to_firestore

        print(json.dumps(upload_to_firestore(project=args.project), indent=2, ensure_ascii=False))
    elif args.cmd == "ask":
        from cinegraph.graph import ask

        result = ask(args.question, force_route=args.route)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "eval":
        from cinegraph.evaluation.run import run_eval

        run_eval(
            configs=args.configs.split(","),
            limit=args.limit,
            ids=args.ids.split(",") if args.ids else None,
            judge=not args.no_judge,
        )
    elif args.cmd == "report":
        from pathlib import Path

        from cinegraph.evaluation.report import regenerate
        from cinegraph.evaluation.run import REPORTS_DIR

        run_dir = Path(args.run_dir) if args.run_dir else max((REPORTS_DIR / "runs").iterdir())
        print(f"Relatório: {regenerate(run_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
