# Avaliação

```bash
uv run cinegraph eval                      # completa: 95 perguntas × 5 configurações + juiz
uv run cinegraph eval --limit 3            # amostra: 3 perguntas de cada categoria
uv run cinegraph eval --configs routed,llm_only --no-judge
uv run cinegraph eval --ids S01,V02,H03
```

Saídas: `reports/runs/<data>/results.jsonl` (cada resposta, com SQL, candidatos, notas do juiz e custo),
`metrics.json`, e o resumo `reports/summary.md` com gráficos.

## Perguntas ([questions.yaml](questions.yaml))

| Categoria | Qtde | Rota esperada | Gabarito |
|---|---|---|---|
| Objetivas (`sql`) | 30 | `sql` | SQL de referência: ranking exato (`ordered`) ou valor (`scalar`) |
| Semânticas (`semantic`) | 30 | `vector` | intenção descrita, julgada por LLM |
| Mistas (`mixed`) | 30 | `hybrid` | intenção julgada + restrições conferidas no banco |
| Fora do escopo (`outro`) | 5 | `outro` | só roteamento |

O conjunto é um rascunho para o grupo revisar (o slide 9 atribui ao grupo a definição das perguntas).

## Configurações

| Nome | O que faz | Hipótese |
|---|---|---|
| `routed` | sistema completo | todas |
| `sql` | força Text-to-SQL | H1 |
| `vector` | força busca vetorial (sem filtros) | H2 |
| `hybrid` | força o modo híbrido | H3 |
| `llm_only` | só o modelo, sem recuperação | H4 (baseline) |

A rota e os filtros são extraídos uma vez por pergunta (na configuração `routed`) e reaproveitados
nas forçadas, que só trocam o mecanismo de recuperação.

## Métricas

- **Acurácia do roteamento:** rota escolhida = rota esperada; matriz de confusão.
- **Acerto exato (objetivas):** para rankings, a lista de filmes recomendada é igual à do gabarito,
  na ordem; para valores, o número da resposta (campo numérico ou texto) bate com o gabarito
  (tolerância de 0,01 ou 0,5%).
- **SQL igual ao gabarito:** o resultado do SQL gerado bate com o do SQL de referência.
- **Precisão@5 (semânticas e mistas):** filmes com relevância ≥ 1 entre os 5 primeiros; listas
  menores contam as posições vazias como erro.
- **Relevante e nos filtros @5 (mistas):** relevância ≥ 1 e todas as restrições satisfeitas.
- **Taxa de alucinação:** itens recomendados com dado fabricado. No baseline: título inexistente no
  acervo, ano errado (±1), gênero que o filme não tem ou nota com erro > 0,3. No CineGraph: IDs fora
  dos candidatos (descartados antes de chegar ao usuário).
- **Latência** p50/p95 e **custo** por pergunta (preços do Sonnet 5.5, com cache).

Intervalos de confiança de 95% por bootstrap sobre as perguntas (`seed = 42`).

## Testes estatísticos ([stats.py](../src/cinegraph/evaluation/stats.py))

Como as configurações respondem às mesmas perguntas, as comparações são pareadas por pergunta, com
α = 5% e correção de Holm para as comparações múltiplas:

| Hipótese | Teste |
|---|---|
| H1 (acerto exato, binário) | McNemar exato |
| H2, H3 (precisão@5, relevante e nos filtros) | Wilcoxon dos postos sinalizados (aproximação normal com correção de empates) |
| H4 (itens com dado errado) | Fisher exato |

Uma hipótese é "confirmada" quando a configuração da hipótese tem média melhor e p ajustado < 0,05
contra cada concorrente. O roteador também é avaliado como classificador: taxa de erro e precisão,
revocação e especificidade de cada rota contra as demais. Os resultados ficam em `tests.json`.

Para refazer o relatório e os testes de uma execução gravada, sem chamar a API:

```bash
uv run cinegraph report reports/runs/20261117-173343
```

## Juiz

O juiz (Sonnet 5.5, esforço baixo) recebe pedido, intenção e filmes (título, ano, gêneros, tags) e dá
0, 1 ou 2 a cada um, podendo usar o próprio conhecimento sobre os filmes. Limitações: pode favorecer
filmes famosos e escolhas parecidas com as que ele faria. Vale revisar uma amostra à mão.
