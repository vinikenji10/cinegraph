"""Validação do SQL gerado pelo modelo (Text-to-SQL controlado).

Regras: uma única instrução, somente SELECT (CTEs e UNION permitidos), apenas
as tabelas do acervo, sem PRAGMA/ATTACH/escrita, e LIMIT obrigatório.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"movies", "genres", "movie_genres", "movie_tags", "ratings"}
DEFAULT_LIMIT = 50
MAX_LIMIT = 50

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
    exp.Pragma, exp.Attach, exp.Detach, exp.Transaction, exp.Commit, exp.Rollback, exp.Merge,
)


class SQLValidationError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Devolve o SQL normalizado e seguro, ou levanta SQLValidationError."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SQLValidationError("Consulta vazia.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLValidationError(f"SQL inválido: {e}") from e
    if len(statements) != 1:
        raise SQLValidationError("Envie exatamente uma consulta.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError("Somente consultas SELECT são permitidas.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise SQLValidationError(f"Operação não permitida: {type(node).__name__}.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise SQLValidationError("Não use nomes de banco qualificados.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SQLValidationError(f"Tabela não permitida: {table.name}.")

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(DEFAULT_LIMIT)
    else:
        try:
            value = int(limit.expression.name)
        except (AttributeError, ValueError):
            value = None
        if value is None or value > MAX_LIMIT:
            tree = tree.limit(MAX_LIMIT)
    return tree.sql(dialect="sqlite")
