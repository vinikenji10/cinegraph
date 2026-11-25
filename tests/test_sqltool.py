import pytest

from cinegraph.sqltool import SQLValidationError, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM movies",
        "DROP TABLE movies",
        "UPDATE movies SET year = 1",
        "INSERT INTO movies (movie_id) VALUES (1)",
        "SELECT 1; DROP TABLE movies",
        "PRAGMA table_info(movies)",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM main.movies",
        "CREATE TABLE x (a INT)",
        "",
    ],
)
def test_blocks_unsafe_sql(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql)


def test_adds_and_caps_limit():
    assert validate_sql("SELECT movie_id FROM movies").endswith("LIMIT 50")
    assert validate_sql("SELECT movie_id FROM movies LIMIT 9999").endswith("LIMIT 50")
    assert validate_sql("SELECT movie_id FROM movies LIMIT 5").endswith("LIMIT 5")


def test_allows_cte_and_aggregates():
    sql = validate_sql("WITH a AS (SELECT movie_id FROM movies) SELECT COUNT(*) FROM a")
    assert sql.startswith("WITH")
