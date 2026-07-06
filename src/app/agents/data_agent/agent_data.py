"""Deep Agent operating over a user's connected data sources (SQL DB + sandboxed folder).

Mirrors the structure of ``text_sql_agent.py`` but is built PER SESSION from the live
resources in the registry, rather than as a singleton bound to a fixed database.
"""

import os
from typing import Any, AsyncGenerator, Optional

from deepagents import create_deep_agent
from langchain.agents.middleware import PIIMiddleware
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

from src.app.agents.data_agent.tools import make_memory_tools
from src.app.agents.tools.search_tool import SearchAPI, get_search_tool
from src.app.core.common.config import settings
from src.app.core.common.graph_utils import process_messages
from src.app.core.common.model.message import Message
from src.app.core.db.readonly import make_readonly_sql_tools
from src.app.core.memory.memory import bg_update_memory, get_relevant_memory
from src.app.core.middleware import (
    AgentContext,
    AgentPipeline,
    ErrorHandlingMiddleware,
    GuardrailMiddleware,
    LoggingMiddleware,
    build_invoke_config,
)
from src.app.core.session.event_recorder import bg_record_tool_event
from src.app.core.session.event_repository import SessionEventRepository

# One stateless repository instance for recording episodic events off the streaming path.
_event_repo = SessionEventRepository()


def load_system_prompt() -> str:
    """Load the Data Agent system prompt."""
    with open(os.path.join(os.path.dirname(__file__), "system.md"), "r", encoding="utf-8") as f:
        return f.read()


class DataAgent:
    """A Deep Agent over one session's connected sources, run through the harness pipeline."""

    def __init__(
        self,
        name: str,
        db: Optional[SQLDatabase] = None,
        backend: Any = None,
        user_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
        agent_id: Optional[int] = None,
        web_search: bool = False,
        memory_enabled: bool = True,
        skills_dir: Optional[str] = None,
        workspace_context: str = "",
    ):
        """Build a Data Agent over a session's sources, isolated to one user and agent."""
        self.name = name
        self.user_id = user_id
        self.agent_id = agent_id
        self.memory_enabled = memory_enabled
        self.agent = _create_data_deep_agent(
            db, backend, user_id, system_prompt, agent_id, web_search, memory_enabled, skills_dir, workspace_context
        )
        self._pipeline = AgentPipeline(
            middlewares=[LoggingMiddleware(), ErrorHandlingMiddleware(), GuardrailMiddleware()],
            invoke_fn=self._core_invoke,
        )

    async def agent_invoke(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[int] = None,
    ) -> list[Message] | list[Any]:
        """Invoke the agent through the harness middleware pipeline (traced by Langfuse)."""
        ctx = AgentContext(
            messages=messages,
            session_id=session_id,
            user_id=user_id,
            config=build_invoke_config(session_id, user_id, self.name),
            agent_name=self.name,
            metadata={"model_name": settings.DEFAULT_LLM_MODEL},
        )
        return await self._pipeline.run(ctx)

    async def _core_invoke(self, ctx: AgentContext) -> list[Message]:
        """Core Deep Agent invocation without cross-cutting concerns."""
        query = ctx.messages[-1].content if ctx.messages else ""
        response = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": query}]},
            config=ctx.config,
        )
        return process_messages(response["messages"])

    async def astream_query_events(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[int] = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the agent's work as structured events for a transparent UI timeline.

        Yields dicts of shape:
          - {"type": "tool_start", "name": str, "input": str}
          - {"type": "tool_end", "name": str, "output": str}
          - {"type": "token", "content": str}   (reasoning + final answer)

        The full message history is passed in, so multi-turn context works without a
        server-side checkpointer. Relevant long-term memory is auto-injected before the turn,
        and the exchange is stored back to memory afterwards. Traced by Langfuse.
        """
        config = build_invoke_config(session_id, user_id, self.name)
        history = [{"role": m.role, "content": m.content} for m in messages]
        last_user = messages[-1].content if messages else ""

        # Auto-inject relevant long-term memory as leading context (scoped to this agent).
        payload_messages = history
        if self.memory_enabled and user_id is not None and last_user:
            relevant = await get_relevant_memory(user_id, last_user, agent_id=self.agent_id)
            if relevant:
                payload_messages = [
                    {"role": "system", "content": f"Contexto do usuário (memória de longo prazo):\n{relevant}"},
                    *history,
                ]

        answer = ""
        async for event in self.agent.astream_events({"messages": payload_messages}, config=config, version="v2"):
            kind = event.get("event")
            if kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = _short(event.get("data", {}).get("input"))
                # Audit trail (#10): record document reads / SQL executions off the hot path.
                bg_record_tool_event(
                    _event_repo,
                    user_id=user_id,
                    agent_id=self.agent_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    scope="database" if tool_name == "run_sql" else "folder",
                )
                yield {
                    "type": "tool_start",
                    "name": tool_name,
                    "input": tool_input,
                }
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                if hasattr(output, "content"):
                    output = output.content
                yield {"type": "tool_end", "name": event.get("name", ""), "output": _short(output)}
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                content = getattr(chunk, "content", "") if chunk is not None else ""
                if content:
                    text = content if isinstance(content, str) else str(content)
                    answer += text
                    yield {"type": "token", "content": text}

        # Store this exchange back into long-term memory (non-blocking), scoped to this agent.
        if self.memory_enabled and user_id is not None and last_user and answer:
            bg_update_memory(
                user_id,
                [{"role": "user", "content": last_user}, {"role": "assistant", "content": answer}],
                {"session_id": session_id, "agent_id": self.agent_id},
                agent_id=self.agent_id,
            )


def _short(value: Any, limit: int = 1500) -> str:
    """Render a tool input/output to a short display string."""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


# Tool-usage guidance the harness ALWAYS appends to a user's custom system prompt, so a
# user-authored persona never drops the mechanics the model needs to actually use its tools.
_HARNESS_CAPABILITIES = """

## Ferramentas desta sessão (não removível)
Conforme as fontes que o usuário conectou, você pode ter:
- **Banco SQL (somente leitura)** — ferramentas `list_tables`, `describe_tables`, `run_sql`
  (apenas `SELECT`/`WITH`/`EXPLAIN`/`SHOW`).
- **Uma pasta concedida** exposta por ferramentas de arquivo (`ls`, `read_file`, `glob`, `grep`)
  em um sandbox isolado montado em `/workspace`.
- **Memória de longo prazo** — `buscar_memoria(consulta)`.

Regras: somente leitura; nunca modifique dados. Para perguntas sobre **arquivos**, use `ls`/`glob`
em `/workspace` e `read_file` para ler o conteúdo (ex.: leia o CSV antes de responder sobre ele);
nunca cite caminhos fora de `/workspace`. Para perguntas de **banco**, SEMPRE `list_tables` →
`describe_tables` (das tabelas que vai usar) → `run_sql`, para a consulta nascer do schema real.
Executar a consulta é a validação: se `run_sql` retornar erro, corrija a partir das tabelas
disponíveis e execute de novo — **nunca invente tabelas ou colunas**, e não dê a resposta final
até a consulta rodar sem erro. Cada resultado de `run_sql` traz uma linha `[proveniência]`;
inclua essa fonte (tabela + consulta) na sua resposta. Seja conciso e cite os arquivos/tabelas usados.
"""


def _compose_system_prompt(system_prompt: Optional[str]) -> str:
    """Return the effective system prompt.

    With no user prompt, use the bundled default (which already documents the tools). With a
    user-authored prompt, keep it as the persona but append the non-removable capabilities
    guidance so the model still knows how to use the filesystem/SQL/memory tools.
    """
    if not system_prompt:
        return load_system_prompt()
    return f"{system_prompt}\n{_HARNESS_CAPABILITIES}"


def _create_data_deep_agent(
    db: Optional[SQLDatabase],
    backend: Any,
    user_id: Optional[int],
    system_prompt: Optional[str] = None,
    agent_id: Optional[int] = None,
    web_search: bool = False,
    memory_enabled: bool = True,
    skills_dir: Optional[str] = None,
    workspace_context: str = "",
) -> Any:
    """Build the underlying Deep Agent with read-only SQL, memory tools, and optional sandbox.

    ``system_prompt`` sets the agent's persona; the harness capabilities guidance is always
    appended so tool usage survives. ``agent_id`` scopes the memory tools per agent.
    ``web_search`` adds a host-side web-search tool (the sandbox stays network-isolated);
    ``memory_enabled`` gates the long-term memory tool. ``skills_dir`` (when set) is a directory
    of SKILL.md files the agent loads via progressive disclosure. ``workspace_context`` (when set)
    is a briefing of the attached sources, prepended so the agent is grounded from the first turn.
    """
    model = ChatOpenAI(model=settings.DEFAULT_LLM_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)
    tools = make_memory_tools(user_id, agent_id) if memory_enabled else []
    if db is not None:
        tools = tools + make_readonly_sql_tools(db)
    if web_search:
        # Runs host-side (not inside the sandbox), so the file sandbox stays --network none.
        tools = tools + get_search_tool(SearchAPI.DUCKDUCKGO)

    prompt = _compose_system_prompt(system_prompt)
    if workspace_context:
        prompt = f"{prompt}\n\n{workspace_context}"

    kwargs: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "system_prompt": prompt,
        "middleware": [PIIMiddleware("email")],
    }
    if skills_dir is not None:
        # deepagents loads SKILL.md files from this directory (progressive disclosure).
        kwargs["skills"] = [skills_dir]
    # When a sandbox backend is provided (phase 2), the built-in filesystem tools
    # (ls/read_file/glob/grep) route into the isolated container.
    if backend is not None:
        kwargs["backend"] = backend

    return create_deep_agent(**kwargs)
