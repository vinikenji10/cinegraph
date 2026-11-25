"""Esquemas das saídas estruturadas do modelo (validadas com Pydantic).

Todos os campos são obrigatórios e anuláveis quando fazem sentido: o modelo
sempre devolve o objeto inteiro, e o código decide o que fazer com `null`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Genre = Literal[
    "Action", "Adventure", "Animation", "Children", "Comedy", "Crime", "Documentary", "Drama",
    "Fantasy", "Film-Noir", "Horror", "IMAX", "Musical", "Mystery", "Romance", "Sci-Fi",
    "Thriller", "War", "Western",
]
Route = Literal["sql", "vector", "hybrid", "outro"]
SortBy = Literal["relevance", "rating", "popularity", "newest", "oldest"]


class Filters(BaseModel):
    genres_all: list[Genre] = Field(description="Gêneros que o filme precisa ter todos")
    genres_any: list[Genre] = Field(description="Basta ter pelo menos um destes gêneros")
    genres_exclude: list[Genre] = Field(description="Gêneros proibidos")
    year_min: int | None = Field(description="Ano de lançamento mínimo (inclusive)")
    year_max: int | None = Field(description="Ano de lançamento máximo (inclusive)")
    min_avg_rating: float | None = Field(description="Nota média mínima, escala 0.5–5")
    min_ratings: int | None = Field(description="Número mínimo de avaliações")

    def is_empty(self) -> bool:
        return not any(
            [self.genres_all, self.genres_any, self.genres_exclude, self.year_min, self.year_max,
             self.min_avg_rating, self.min_ratings]
        )

    @classmethod
    def empty(cls) -> "Filters":
        return cls(genres_all=[], genres_any=[], genres_exclude=[], year_min=None, year_max=None,
                   min_avg_rating=None, min_ratings=None)


class RoutePlan(BaseModel):
    route: Route
    reason: str = Field(description="Uma frase explicando a escolha da rota")
    semantic_query: str | None = Field(
        description="Tema/atmosfera/enredo pedido, reescrito em inglês como palavras-chave no estilo das tags do MovieLens"
    )
    reference_titles: list[str] = Field(description="Filmes citados como referência de semelhança, título em inglês")
    filters: Filters
    sort_by: SortBy
    limit: int = Field(description="Quantidade de filmes pedida (padrão 5, máximo 20)")
    use_previous_results: bool = Field(description="true se a pergunta refina a resposta anterior")


class SQLDraft(BaseModel):
    sql: str = Field(description="Uma única consulta SELECT em SQLite")
    explanation: str = Field(description="Uma frase dizendo o que a consulta faz")


class Recommendation(BaseModel):
    movie_id: int
    reason: str = Field(description="Uma frase com a evidência (tags, gêneros, nota) que justifica a escolha")


class AnswerDraft(BaseModel):
    text: str = Field(description="Resposta curta em português do Brasil")
    recommendations: list[Recommendation]
    numeric_answer: float | None = Field(description="Valor numérico principal, se a pergunta pede um número")


class BaselineRecommendation(BaseModel):
    title: str = Field(description="Título original em inglês")
    year: int | None
    genres: list[str]
    avg_rating: float | None = Field(description="Nota média aproximada no MovieLens, 0.5–5")
    reason: str


class BaselineAnswer(BaseModel):
    text: str
    recommendations: list[BaselineRecommendation]
    numeric_answer: float | None
