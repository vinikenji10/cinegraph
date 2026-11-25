import sqlite3

import pytest

from cinegraph import db
from cinegraph.graph import ask
from cinegraph.schemas import Filters
from conftest import FakeLLM, make_plan

pytestmark = pytest.mark.needs_data

ACTION_TOP = (
    "SELECT m.movie_id, m.display_title, m.year, m.bayes_rating FROM movies m "
    "WHERE m.year > 2015 AND EXISTS (SELECT 1 FROM movie_genres g WHERE g.movie_id = m.movie_id "
    "AND g.genre = 'Action') ORDER BY m.bayes_rating DESC, m.movie_id ASC LIMIT 5"
)


def test_dataset_counts_match_readme():
    _, rows, _ = db.run_query("SELECT (SELECT COUNT(*) FROM movies), (SELECT COUNT(*) FROM ratings), "
                              "(SELECT SUM(n_applications) FROM movie_tags), (SELECT COUNT(DISTINCT user_id) FROM ratings)")
    assert rows[0] == (9742, 100836, 3683, 610)


def test_connection_is_read_only():
    with pytest.raises(sqlite3.DatabaseError):
        db.connect().execute("DELETE FROM movies")


def test_title_matching():
    assert db.match_title("Matrix") == 2571
    assert db.match_title("Eternal Sunshine of the Spotless Mind") == 7361
    # Formato do MovieLens, com o artigo no fim: o roteador às vezes devolve assim.
    assert db.match_title("Matrix, The") == 2571
    assert db.match_title("Godfather, The (1972)") == 858
    assert db.match_title("Bamba, La") == 3478
    assert db.match_title("La Bamba") == 3478
    assert db.match_title("Um filme que não existe de jeito nenhum") is None


def test_filters():
    f = Filters.empty().model_copy(update={"year_min": 2000, "genres_all": ["Sci-Fi"]})
    assert db.filter_ids([2571, 7361, 79132, 1], f) == {7361, 79132}


def test_sql_route_keeps_sql_order():
    llm = FakeLLM(make_plan("sql"), sql=ACTION_TOP)
    out = ask("top 5 filmes de ação depois de 2015", llm=llm, vector_backend="local")
    assert out["route"] == "sql"
    ids = [r["movie_id"] for r in out["answer"]["recommendations"]]
    _, rows, _ = db.run_query(ACTION_TOP)
    assert ids == [r[0] for r in rows]
    assert all(r["year"] > 2015 and "Action" in r["genres"] for r in out["answer"]["recommendations"])


def test_sql_retry_then_error_message():
    llm = FakeLLM(make_plan("sql"), sql="DELETE FROM movies")
    out = ask("apague tudo", llm=llm, vector_backend="local")
    assert llm.calls.count("sql") == 1 and llm.calls.count("sql_retry") == 1
    assert out["sql_error"] and out["answer"]["recommendations"] == []


def test_vector_route_and_reference_title_excluded():
    llm = FakeLLM(make_plan("vector", semantic="virtual reality, philosophy, cyberpunk", refs=["The Matrix"]))
    out = ask("algo como Matrix", llm=llm, vector_backend="local")
    ids = [r["movie_id"] for r in out["answer"]["recommendations"]]
    assert ids and 2571 not in ids
    assert out["retrieval"]["reference_ids"] == [2571]
    # A redação recebe a referência à parte, para explicar a semelhança sem recomendá-la.
    payload = llm.payloads[-1]
    assert [m["movie_id"] for m in payload["reference_movies"]] == [2571]
    assert 2571 not in [c["movie_id"] for c in payload["candidates"]]
    assert "reference_not_in_collection" not in payload


def test_reference_title_missing_from_collection_is_reported():
    plan = make_plan("vector", semantic="space opera, epic", refs=["Dune: Part Two"])
    llm = FakeLLM(plan)
    out = ask("algo como Duna Parte Dois", llm=llm, vector_backend="local")
    assert out["retrieval"]["reference_ids"] == []
    assert out["retrieval"]["reference_unmatched"] == ["Dune: Part Two"]
    assert llm.payloads[-1]["reference_not_in_collection"] == ["Dune: Part Two"]
    assert "reference_movies" not in llm.payloads[-1]


def test_hybrid_applies_exact_filters():
    plan = make_plan("hybrid", semantic="melancholy, memory, bittersweet", year_min=2000, genres_all=["Drama"])
    out = ask("drama melancólico depois de 2000", llm=FakeLLM(plan), vector_backend="local")
    recs = out["answer"]["recommendations"]
    assert recs and all(r["year"] >= 2000 and "Drama" in r["genres"] for r in recs)


def test_answer_cannot_cite_unretrieved_movie():
    plan = make_plan("vector", semantic="space adventure")
    out = ask("aventura espacial", llm=FakeLLM(plan, bogus_id=999_999_999), vector_backend="local")
    assert 999_999_999 not in [r["movie_id"] for r in out["answer"]["recommendations"]]
    assert out["answer"]["dropped_ids"] == [999_999_999]


def test_forced_route_overrides_classifier():
    out = ask("top 5 de ação", llm=FakeLLM(make_plan("sql", semantic=None)), force_route="vector", vector_backend="local")
    assert out["route"] == "vector" and out["forced"] == "vector"


def test_previous_results_are_refined():
    plan = make_plan("hybrid", semantic="dark", previous=True, year_min=2000)
    history = [{"question": "q", "answer": "a", "movies": [{"movie_id": 2571, "title": "The Matrix", "year": 1999},
                                                           {"movie_id": 79132, "title": "Inception", "year": 2010}]}]
    out = ask("e só os depois de 2000?", history=history, llm=FakeLLM(plan), vector_backend="local")
    assert [r["movie_id"] for r in out["answer"]["recommendations"]] == [79132]


def test_outro_route_skips_answer_llm():
    llm = FakeLLM(make_plan("outro"))
    out = ask("quem dirigiu Matrix?", llm=llm, vector_backend="local")
    assert llm.calls == ["route"] and out["answer"]["recommendations"] == []


def test_baseline_checks_hallucinations():
    llm = FakeLLM(make_plan("sql"))
    out = ask("recomende ficção científica", llm=llm, force_route="llm_only", vector_backend="local")
    recs = out["answer"]["recommendations"]
    assert recs[0]["movie_id"] == 2571 and recs[0]["checks"]["exists"]
    assert recs[1]["movie_id"] is None and not recs[1]["checks"]["exists"]
