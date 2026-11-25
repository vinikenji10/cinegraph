# Plano de Gestão de Dados (DMP) — CineGraph

Como o projeto obtém, transforma, protege e preserva seus dados. Versão de 10/11/2026.

## 1. Fonte e licença

| Item | Valor |
|---|---|
| Conjunto | MovieLens Latest Small (`ml-latest-small`), GroupLens Research, Universidade de Minnesota |
| Origem | https://files.grouplens.org/datasets/movielens/ml-latest-small.zip |
| Conteúdo | 9.742 filmes, 100.836 avaliações de 610 usuários (1996–2018), 3.683 tags, links para IMDb e TMDb |
| Snapshot congelado | baixado em 10/11/2026; MD5 do zip `31a303aabbc519bd33d025e44d6c2570` (confere com o publicado); SHA-256 `696d65a3dfceac7c45750ad32df2c259311949efec81f0f144fdfb91ebc9e436` |
| Licença | uso em pesquisa/ensino; é proibido redistribuir sem permissão; citação obrigatória (Harper & Konstan, 2015) |

O "Latest" muda com o tempo, então o projeto trabalha sobre o snapshot acima. `cinegraph download`
grava `data/raw/MANIFEST.json` com data, MD5, SHA-256 do zip e de cada CSV, e contagem de linhas.
Execuções seguintes conferem os hashes e falham se o snapshot tiver mudado.

Os dados **não vão para o repositório git** (licença). Cada integrante recria tudo com os scripts.

## 2. Artefatos derivados

| Artefato | Onde | Como é gerado |
|---|---|---|
| CSVs brutos | `data/raw/ml-latest-small/` (somente leitura) | `cinegraph download` |
| Relatório de preparação | `data/processed/prep_report.json` | `cinegraph build-db` |
| Banco SQLite | `data/processed/cinegraph.db` | `cinegraph build-db` (tabelas no [dicionário](dicionario_dados.md)) |
| Perfis textuais | `data/processed/profiles.jsonl` | `cinegraph build-index` |
| Embeddings | `data/processed/embeddings.npy` (+ `embedding_ids.npy`, `index_meta.json` com SHA-256) | `cinegraph build-index` |
| Índice vetorial | Firestore, coleção `movies` (9.742 documentos, vetor de 385 dimensões) | `cinegraph upload-index` |
| Perguntas de teste | `eval/questions.yaml` (versionado) | escrito pelo grupo |
| Resultados da avaliação | `reports/runs/<data>/` (bruto, fora do git) e `reports/summary.md` (versionado) | `cinegraph eval` |
| Logs de uso do site | Firestore: `users/{uid}/conversations/{id}/turns`, `usage/{uid}_{dia}` | API no Cloud Run |

Limpeza aplicada (canvas, "Preparação"): linhas com campos obrigatórios em branco são removidas
tabela a tabela (no snapshot atual, nenhuma); tags são normalizadas (minúsculas, espaços);
"(no genres listed)" vira lista vazia; o ano é extraído do título. **Filmes sem tag não são
descartados**: só 1.572 dos 9.742 filmes têm tags, e descartá-los eliminaria 84% do acervo.

## 3. Armazenamento e acesso

- **Código e configuração:** Git (este repositório). Segredos ficam fora: `.env` local (ignorado
  pelo git) e Google Secret Manager (`anthropic-api-key`) em produção.
- **Dados brutos:** só leitura no disco de cada integrante; recriáveis a qualquer momento.
- **Banco SQLite:** aberto em modo `ro`, com `PRAGMA query_only` e autorizador que só permite
  leitura; o SQL gerado pelo modelo ainda passa por validação (`sqltool.py`).
- **Firestore:** regras negam todo acesso direto do navegador. Só o backend, com a conta de serviço
  `cinegraph-api@cinegraph-ptc3567.iam.gserviceaccount.com` (papel `datastore.user`), lê e grava.
- **Site:** login Google via Firebase Auth, restrito a uma lista de e-mails; cota diária por usuário.
- **Backup:** os dados brutos e derivados são reconstruíveis pelos scripts; o código vive no git,
  com um repositório remoto privado compartilhado pelo grupo como cópia de segurança. O histórico de
  conversas no Firestore é descartável.

## 4. Reprodução e preservação

Tudo que influencia os resultados está fixado e versionado:

| O quê | Onde |
|---|---|
| Versões das bibliotecas | `uv.lock` (e `requirements.txt` gerado dele no deploy) |
| Modelo de linguagem | `claude-sonnet-5-5`, configurável por `CINEGRAPH_LLM_MODEL`; esforço por etapa em `llm.py` |
| Modelo de embeddings | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (fastembed) |
| Prompts | `prompts.py` (`PROMPT_VERSION`) e `evaluation/judge.py` (`JUDGE_PROMPT_VERSION`) |
| Parâmetros | média bayesiana `m = 10`; peso de popularidade `s = 0,10`; `K` da busca em `graph.py` |
| Sementes | bootstrap da avaliação com `seed = 42`; a busca vetorial é exata (sem aleatoriedade) |
| Metadados de cada execução | `reports/runs/<data>/metrics.json` registra modelo, versões de prompt e custo |

Sequência completa de reprodução:

```bash
uv sync && uv run cinegraph download && uv run cinegraph build-db && uv run cinegraph build-index && uv run cinegraph eval
```

O modelo de linguagem é um serviço externo e pode variar entre execuções mesmo com o mesmo prompt;
por isso cada execução da avaliação guarda as respostas brutas.

## 5. Privacidade e responsabilidade

- O MovieLens usa identificadores anônimos de usuário; não há dados pessoais nem sensíveis no acervo.
- O site guarda, por usuário autenticado, as perguntas feitas, a resposta, a rota e o custo, para
  avaliação e controle de gasto. Não guarda e-mail no histórico (só o `uid` do Firebase).
- As perguntas são enviadas à API da Anthropic para gerar a resposta.
- O chatbot não inventa títulos, notas ou anos: o modelo só escolhe entre filmes recuperados, e os
  dados exibidos vêm do banco. IDs fora da lista são descartados e contabilizados na avaliação.
