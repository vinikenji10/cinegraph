# Decisões técnicas e experimentos

Registro das escolhas que não são óbvias pelo código, com o motivo de cada uma.

## Dados

**Média bayesiana para "mais bem avaliados".** Com a média simples, o topo é dominado por filmes
com uma única nota 5. A média ponderada com `m = 10` puxa filmes com poucas avaliações para a média
global (3,50). Resultado: o topo geral vira *Um Sonho de Liberdade*, *O Poderoso Chefão*, *Clube da
Luta*. O prompt de SQL e o gabarito da avaliação usam a mesma convenção.

**Não descartar filmes sem tag.** O canvas pedia "remover linhas com dados em branco". Aplicado ao
resultado da junção, isso deixaria só 1.572 filmes (16%). A remoção foi feita tabela a tabela
(nenhuma linha estava em branco no snapshot).

## Busca semântica

**Modelo de embeddings.** O pitch citava um modelo multilíngue local. O `multilingual-e5-small` não
está disponível no fastembed (ONNX, sem PyTorch, o que deixa o container leve). Testamos
`paraphrase-multilingual-MiniLM-L12-v2` (384 dim., 0,22 GB) e `paraphrase-multilingual-mpnet-base-v2`
(768 dim., 1 GB). O mpnet não foi claramente melhor nas consultas de teste e pesa cinco vezes mais;
ficamos com o MiniLM.

**Perfil textual.** Primeira versão: título, ano, gêneros e as 15 primeiras tags. Três problemas:

1. O corte em 15 tags era alfabético quando as tags empatavam (quase sempre uma aplicação cada):
   *Brilho Eterno de uma Mente sem Lembranças* perdia justamente `memory` e `melancholy`.
2. Palavras do título geravam casamentos falsos: "comédia em família" trazia *The Quiet Family*,
   *Family Business*; "terror perturbador" trazia *Disturbing Behavior*.
3. Pergunta em português × tags em inglês casavam mal.

Versão final: tags primeiro (até 30, ordenadas por número de usuários), depois gêneros em inglês e
português; título só quando o filme não tem tag nem gênero. O classificador reescreve o pedido como
palavras-chave em inglês no estilo das tags (`semantic_query`). Exemplo, "algo melancólico sobre
memória e identidade": antes, *Regarding Henry*, *Obsession*, *Mortal Thoughts*; depois, com a
consulta reescrita, *Requiem for a Dream*, *Memento*, *Brilho Eterno*, *Donnie Darko*.

**Prior de popularidade dentro do vetor.** Sem nenhum peso de popularidade, filmes obscuros com uma
avaliação apareciam no topo. Aplicar o peso depois da busca tem um defeito: muitos filmes sem tag
têm perfil idêntico e, portanto, a mesma similaridade, e o Firestore devolvia um subconjunto
arbitrário desses empates (conferido: as similaridades batiam com a busca local, só o desempate
mudava). A solução foi acrescentar uma dimensão ao vetor:

```
v' = [v·√0,9 ; √0,1·p]     q' = [q·√0,9 ; √0,1]     q'·v' = 0,9·cos(q,v) + 0,1·p
p = ln(1 + avaliações) / ln(1 + 329)
```

Com produto interno, o próprio Firestore ordena por similaridade + popularidade, e a busca local e a
do Firestore passam a devolver a mesma ordem. O peso 0,1 equivale ao bônus testado antes
(`0,02·ln(1+n)`).

**Firestore em vez de ChromaDB.** Os dois são gratuitos para este volume. O Firestore foi escolhido
porque o site já roda no Firebase: um serviço a menos e o índice visível no console. Custo por
busca: 1 leitura a cada 100 entradas do índice (~98 para o acervo inteiro) + 1 por documento
devolvido. Na cota gratuita (50 mil leituras/dia) cabem centenas de perguntas por dia. A avaliação
usa a cópia local dos vetores para não gastar essa cota.

## Text-to-SQL

- Uma única instrução `SELECT` (CTEs e `UNION` permitidos), validada com `sqlglot`: tabelas da lista,
  sem nomes qualificados, sem escrita, `PRAGMA` ou `ATTACH`, `LIMIT` obrigatório (máximo 50).
- Conexão SQLite em modo `ro`, `PRAGMA query_only = ON` e um autorizador que nega qualquer ação que
  não seja leitura. Tempo máximo de 3 s por consulta.
- Se a consulta falhar (validação ou execução), o erro volta ao modelo uma única vez.
- Na rota SQL, a ordem do resultado é a resposta: o modelo só redige o texto e as justificativas.

## Modo híbrido

A busca vetorial traz os 200 candidatos mais próximos; os filtros exatos (gênero, ano, nota,
número de avaliações) são aplicados em SQL parametrizado, sem SQL gerado pelo modelo. Se sobrarem
menos filmes do que o pedido, a busca é refeita com 1.000 candidatos (o máximo do Firestore). Os
15 melhores vão para o modelo, que escolhe e justifica.

### Filmes de referência ("algo como Matrix")

O vetor do filme citado é somado ao da pergunta, e o próprio filme sai dos candidatos para não ser
recomendado de volta. Em 11/11 vimos dois problemas:

- O roteador às vezes devolve o título no formato do MovieLens ("Matrix, The"), e a busca por
  título não o reconhecia. `normalize_title` agora traz o artigo do fim para o começo.
- A redação não sabia que a referência tinha sido retirada e respondia "Matrix não aparece entre
  os candidatos". Agora ela recebe `reference_movies` (gêneros e tags da referência, para explicar
  a semelhança) e `reference_not_in_collection` (títulos citados que não estão no acervo). Prompts
  na versão 2026-11-11.1.

## Modelo de linguagem

- `claude-sonnet-5-5` em todas as etapas, escolhido pelo grupo.
- Saídas estruturadas (Pydantic) em todas as chamadas: rota e filtros, SQL, resposta e juiz.
- Esforço `low` no classificador, na resposta e no juiz; `medium` na geração de SQL.
- Cache de prompt nos textos de sistema, que não têm nada variável.
- Fallback do servidor (`fallbacks: "default"`) para o caso raro de recusa por política.
- A resposta só pode citar `movie_id` entre os candidatos; outros são descartados e contados.

## Segurança do site

- Login Google (Firebase Auth); a API confere o token e exige e-mail verificado em uma lista.
- Cota diária por usuário, para limitar o gasto com a API.
- O navegador não acessa o Firestore (regras negam tudo); só o backend, com conta de serviço própria.
- A chave da API fica no Secret Manager, injetada no Cloud Run.
