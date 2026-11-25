import pytest

from cinegraph.config import get_settings
from cinegraph.schemas import AnswerDraft, BaselineAnswer, BaselineRecommendation, Filters, Recommendation, RoutePlan, SQLDraft


def pytest_collection_modifyitems(config, items):
    if not get_settings().db_path.exists() or not get_settings().embeddings_path.exists():
        skip = pytest.mark.skip(reason="rode `uv run cinegraph download build-db build-index` antes")
        for item in items:
            if "needs_data" in item.keywords:
                item.add_marker(skip)


def make_plan(route="sql", semantic=None, refs=None, limit=5, sort_by="relevance", previous=False, **filters):
    f = Filters.empty().model_copy(update=filters)
    return RoutePlan(route=route, reason="teste", semantic_query=semantic, reference_titles=refs or [],
                     filters=f, sort_by=sort_by, limit=limit, use_previous_results=previous)


class FakeLLM:
    """Modelo falso: devolve respostas fixas e registra as chamadas."""

    def __init__(self, plan: RoutePlan, sql: str = "SELECT 1", pick_all: bool = True, bogus_id: int | None = None):
        self.plan, self.sql, self.pick_all, self.bogus_id = plan, sql, pick_all, bogus_id
        self.calls: list[str] = []
        self.payloads: list[dict] = []

    def route(self, question, history):
        self.calls.append("route")
        return self.plan, None

    def draft_sql(self, question, plan, history, previous_ids, error=None, failed_sql=None):
        self.calls.append("sql_retry" if error else "sql")
        return SQLDraft(sql=self.sql, explanation="teste"), None

    def answer(self, question, route, payload, limit):
        self.calls.append("answer")
        self.payloads.append(payload)
        ids = [c["movie_id"] for c in payload["candidates"]][:limit]
        recs = [Recommendation(movie_id=i, reason="evidência") for i in ids]
        if self.bogus_id is not None:
            recs.insert(0, Recommendation(movie_id=self.bogus_id, reason="inventado"))
        return AnswerDraft(text="ok", recommendations=recs, numeric_answer=None), None

    def baseline(self, question):
        self.calls.append("baseline")
        recs = [
            BaselineRecommendation(title="The Matrix", year=1999, genres=["Action", "Sci-Fi"], avg_rating=4.2, reason="x"),
            BaselineRecommendation(title="A Movie That Does Not Exist", year=2011, genres=["Drama"], avg_rating=4.0, reason="y"),
        ]
        return BaselineAnswer(text="ok", recommendations=recs, numeric_answer=None), None
