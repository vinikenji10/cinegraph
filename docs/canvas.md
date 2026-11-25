# Canvas do fluxo de Ciência de Dados — CineGraph

Objetivos primeiro; depois, dados, preparação, modelagem e avaliação. Cada bloco aponta para onde
foi implementado.

## 1. Declaração do problema

- Filtros objetivos não capturam tema, atmosfera ou semelhança.
- O modelo de linguagem sozinho pode inventar títulos, notas ou atributos.
- O chatbot deve selecionar a recuperação adequada para cada pergunta.

Pergunta de pesquisa: como combinar consultas estruturadas e busca semântica para gerar
recomendações relevantes e verificáveis?

## 2. Resultados e previsões

- **Entrada X:** pergunta, contexto da conversa (últimas 3 trocas, com os filmes mostrados) e
  preferências declaradas.
- **Saída y:** rota `sql`, `vector`, `hybrid` (ou `outro`), com filtros extraídos — `RoutePlan` em
  [`schemas.py`](../src/cinegraph/schemas.py).
- **Resultado:** lista ranqueada de filmes do acervo, cada um com justificativa baseada em gêneros,
  tags e notas, e o rastro da decisão (rota, SQL, candidatos, latência, custo).

## 3. Aquisição de dados

- MovieLens Latest Small (GroupLens): `movies.csv`, `ratings.csv`, `tags.csv`, `links.csv`.
- Snapshot fixo com checksum: [`data/download.py`](../src/cinegraph/data/download.py) e
  [DMP](DMP.md).

## 4. Preparação dos dados

- Juntar os CSVs por `movieId`; extrair ano e título de exibição; separar gêneros.
- Remover linhas com campos obrigatórios em branco, tabela a tabela (sem descartar filmes sem tag).
- Agregar notas: contagem, média simples e média bayesiana (`m = 10`).
- Agregar tags por filme (aplicações e usuários distintos).
- Montar o SQLite ([`data/prepare.py`](../src/cinegraph/data/prepare.py)) e um perfil textual por
  filme ([`retrieval/index.py`](../src/cinegraph/retrieval/index.py)).

## 5. Modelagem

- **LangGraph** classifica a intenção e controla o fluxo ([`graph.py`](../src/cinegraph/graph.py)).
- **Text-to-SQL** atende filtros, agregações e rankings, com validação e conexão somente leitura.
- **Embeddings + banco vetorial** (Firestore) atendem pedidos semânticos; o vetor carrega um
  componente de popularidade.
- **Modo híbrido:** candidatos semânticos → filtros exatos em SQL parametrizado → o modelo escolhe e
  justifica.
- **Ativação:** site no Firebase Hosting com backend no Cloud Run ([`api.py`](../src/cinegraph/api.py)).

## 6. Avaliação do modelo

| Métrica | Como é medida |
|---|---|
| Acurácia do roteamento | rota escolhida × rota esperada nas 95 perguntas, com matriz de confusão |
| Correção dos dados (SQL) | resultado comparado ao SQL de referência (ranking exato ou valor) |
| Relevância | precisão@5 dada por um juiz LLM, nas perguntas semânticas e mistas |
| Respeito aos filtros | restrições das perguntas mistas conferidas no banco |
| Taxa de alucinação | itens com título inexistente ou ano/gênero/nota errados, contra o baseline só-LLM |
| Tempo de resposta e custo | p50/p95 por configuração e US$ por pergunta |

Detalhes em [eval/README.md](../eval/README.md); resultados em [reports/summary.md](../reports/summary.md).
