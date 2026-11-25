# Dicionário de dados

## SQLite (`data/processed/cinegraph.db`)

### `movies` — um registro por filme (9.742)

| Coluna | Tipo | Descrição |
|---|---|---|
| `movie_id` | INTEGER PK | `movieId` do MovieLens |
| `title` | TEXT | título original do MovieLens, ex. `Matrix, The (1999)` |
| `display_title` | TEXT | título para exibição, artigo no início e sem ano, ex. `The Matrix` |
| `year` | INTEGER | ano de lançamento extraído do título (NULL em 12 filmes) |
| `genres` | TEXT | gêneros separados por `|` (vazio em 34 filmes) |
| `n_ratings` | INTEGER | número de avaliações (0 em 18 filmes) |
| `avg_rating` | REAL | média simples das notas, 0,5–5 (NULL sem avaliações) |
| `bayes_rating` | REAL | média bayesiana `v/(v+m)·R + m/(v+m)·C`, com `m = 10` e `C = 3,5016` (média global) |
| `n_tags` | INTEGER | número de tags distintas |
| `imdb_id` | TEXT | ID do IMDb no formato `tt0133093` |
| `tmdb_id` | INTEGER | ID do TMDb (NULL em 8 filmes) |

### `genres` — 19 gêneros

| Coluna | Tipo | Descrição |
|---|---|---|
| `genre` | TEXT PK | nome em inglês, como no MovieLens |
| `genre_pt` | TEXT | tradução usada no prompt e na interface |

### `movie_genres` — pares filme–gênero

| Coluna | Tipo | Descrição |
|---|---|---|
| `movie_id` | INTEGER | FK para `movies` |
| `genre` | TEXT | FK para `genres` |

### `movie_tags` — tags agregadas por filme (3.574 pares)

| Coluna | Tipo | Descrição |
|---|---|---|
| `movie_id` | INTEGER | FK para `movies` |
| `tag` | TEXT | tag normalizada (minúsculas, espaços únicos), em inglês |
| `n_applications` | INTEGER | quantas vezes a tag foi aplicada ao filme |
| `n_users` | INTEGER | quantos usuários distintos aplicaram a tag |

### `ratings` — avaliações individuais (100.836)

| Coluna | Tipo | Descrição |
|---|---|---|
| `user_id` | INTEGER | ID anônimo do usuário (610 usuários) |
| `movie_id` | INTEGER | FK para `movies` |
| `rating` | REAL | nota de 0,5 a 5, em passos de 0,5 |
| `rated_at` | TEXT | data da avaliação (`YYYY-MM-DD`, UTC) |
| `rated_year` | INTEGER | ano da avaliação (não é o ano do filme) |

### `metadata`

Pares chave–valor: data de construção, SHA-256 do snapshot, parâmetros da média bayesiana.

## Arquivos do índice (`data/processed/`)

| Arquivo | Conteúdo |
|---|---|
| `profiles.jsonl` | `{movie_id, text}`: texto vetorizado (tags, depois gêneros; título só se não houver mais nada) |
| `embeddings.npy` | matriz `float32` 9.742 × 385: 384 dimensões de conteúdo × √0,9 e uma de popularidade |
| `embedding_ids.npy` | `movie_id` de cada linha |
| `index_meta.json` | modelo, dimensões, `n_ratings_max`, peso de popularidade, SHA-256 da matriz |

## Firestore (projeto `cinegraph-ptc3567`)

| Caminho | Campos | Uso |
|---|---|---|
| `movies/{movie_id}` | `movie_id`, `display_title`, `year`, `genres[]`, `n_ratings`, `embedding` (vetor 385) | busca vetorial (`find_nearest`, produto interno) |
| `_meta/index` | cópia de `index_meta.json` | evita reenviar vetores iguais |
| `users/{uid}/conversations/{id}` | `updated_at` | conversa do site |
| `users/{uid}/conversations/{id}/turns/{auto}` | `question`, `answer`, `movies[]`, `route`, `sql`, `latency_s`, `cost_usd`, `error`, `created_at` | histórico e contexto da conversa |
| `usage/{uid}_{AAAA-MM-DD}` | `uid`, `day`, `count` | cota diária por usuário |
