"""Read-only SQL guard and tool factory for user-connected databases.

Defense-in-depth: even if the connected DB user is read-only, we reject any
statement that is not a single read query before executing it.
"""

import re

from langchain_community.utilities import SQLDatabase
from langchain_core.tools import BaseTool, tool

_ALLOWED_PREFIXES = ("select", "with", "explain", "show", "table", "values")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"attach|copy|call|merge|replace|vacuum|reindex|comment|lock|set)\b",
    re.IGNORECASE,
)


class SqlNotReadOnlyError(ValueError):
    """Raised when a SQL statement is not an allowed read-only query."""


def assert_read_only(sql: str) -> None:
    """Raise SqlNotReadOnlyError unless ``sql`` is a single read-only statement.

    Args:
        sql: The SQL string to validate.

    Raises:
        SqlNotReadOnlyError: If the statement writes, or bundles several statements.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise SqlNotReadOnlyError("Consulta vazia.")
    if ";" in stripped:
        raise SqlNotReadOnlyError("Apenas uma instrução por consulta é permitida.")
    lowered = stripped.lstrip("(").lstrip().lower()
    if not lowered.startswith(_ALLOWED_PREFIXES):
        raise SqlNotReadOnlyError("Apenas consultas de leitura (SELECT/WITH/EXPLAIN/SHOW) são permitidas.")
    if _FORBIDDEN.search(stripped):
        raise SqlNotReadOnlyError("A consulta contém uma operação de escrita não permitida.")


def make_readonly_sql_tools(db: SQLDatabase) -> list[BaseTool]:
    """Build read-only SQL tools bound to a specific connected database.

    Args:
        db: The SQLDatabase to expose (already connected).

    Returns:
        A list of tools: list_tables, describe_tables, run_sql (read-only).
    """

    @tool
    def list_tables() -> str:
        """List the tables available in the connected database."""
        return ", ".join(db.get_usable_table_names()) or "(nenhuma tabela)"

    @tool
    def describe_tables(tables: str) -> str:
        """Return schema and sample rows for comma-separated table names."""
        names = [t.strip() for t in tables.split(",") if t.strip()]
        try:
            return db.get_table_info(names or None)
        except Exception as exc:  # noqa: BLE001 - surface a readable message to the LLM
            return f"Erro ao descrever tabelas: {exc}"

    @tool
    def run_sql(query: str) -> str:
        """Run a READ-ONLY SQL query (SELECT/WITH/EXPLAIN/SHOW only) and return the rows."""
        try:
            assert_read_only(query)
        except SqlNotReadOnlyError as exc:
            return f"Consulta rejeitada: {exc}"
        try:
            return db.run(query)
        except Exception as exc:  # noqa: BLE001 - surface a readable message to the LLM
            return f"Erro ao executar a consulta: {exc}"

    return [list_tables, describe_tables, run_sql]
