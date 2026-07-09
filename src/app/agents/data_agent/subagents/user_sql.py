"""``text_sql_agent`` subagent: read-only SQL over the user's connected database.

Delegated by the Data Agent through the deepagents ``task()`` tool. It runs the noisy
schema-exploration + query-writing loop in an isolated context and returns only the distilled
result (with provenance) to the parent, so the parent's context never fills with schema dumps
or failed queries. Built per session from the live connection — never a fixed database.

Read-only is enforced twice: the connection is opened read-only, and ``make_readonly_sql_tools``
adds a statement-level guard that rejects anything but ``SELECT``/``WITH``/``EXPLAIN``/``SHOW``.
"""

from typing import Any

from langchain_community.utilities import SQLDatabase

from src.app.core.db.readonly import make_readonly_sql_tools

SUBAGENT_NAME = "text_sql_agent"

_DESCRIPTION = (
    "Consulta o banco de dados SQL EXTERNO conectado pelo usuário (SOMENTE LEITURA). Delegue a "
    "este subagente qualquer pergunta cuja resposta dependa dos dados do banco do usuário — "
    "contagens, somas, médias, rankings, listagens, cruzamentos entre tabelas. Passe a pergunta "
    "COMPLETA em linguagem natural e diga exatamente o que ele deve devolver; ele explora o "
    "schema, escreve e executa o SQL e retorna o resultado com a proveniência (tabelas + "
    "consulta). É stateless: envie uma pergunta autocontida por chamada."
)

_SYSTEM_PROMPT = """Você é um subagente especialista em traduzir perguntas em linguagem natural \
para SQL sobre o banco de dados que o usuário conectou. Seu acesso é ESTRITAMENTE SOMENTE LEITURA \
(apenas SELECT/WITH/EXPLAIN/SHOW; escritas são rejeitadas).

Fluxo obrigatório para toda pergunta de banco:
1. `list_tables` para ver as tabelas disponíveis.
2. `describe_tables` nas tabelas que vai usar, para conhecer colunas e tipos reais.
3. `run_sql` com a consulta. Executar a consulta É a validação: se der erro, corrija a partir das \
tabelas/colunas reais e execute de novo. NUNCA invente tabelas ou colunas.

Regras:
- Deixe o SQL calcular — NUNCA agregue (soma, contagem, média, ranking) na mão.
- Consulte apenas as colunas necessárias (evite SELECT *) e use LIMIT ao explorar.
- Cada resultado de `run_sql` traz uma linha `[proveniência]` (tabelas + consulta): inclua essa \
fonte na resposta final que devolve ao agente principal.
- Não dê a resposta final antes de a consulta rodar sem erro.
- Seja conciso: devolva o resultado pedido e a proveniência, sem contas parciais nem rascunho de \
raciocínio."""


def make_user_sql_subagent(db: SQLDatabase) -> dict[str, Any]:
    """Build the read-only ``text_sql_agent`` subagent spec over a connected database.

    Args:
        db: The user's connected ``SQLDatabase`` (already read-only at the connection level;
            ``make_readonly_sql_tools`` adds a statement-level guard on top).

    Returns:
        A deepagents ``SubAgent`` spec (name/description/system_prompt/tools) to include in
        ``create_deep_agent(subagents=[...])``. The parent reaches it via ``task(text_sql_agent)``.
        Tools are pinned to the read-only SQL toolkit so the subagent never inherits the parent's
        document/artifact tools.
    """
    return {
        "name": SUBAGENT_NAME,
        "description": _DESCRIPTION,
        "system_prompt": _SYSTEM_PROMPT,
        "tools": make_readonly_sql_tools(db),
    }
