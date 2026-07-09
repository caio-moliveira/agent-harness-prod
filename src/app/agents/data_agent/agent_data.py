"""Deep Agent operating over a user's connected data sources (SQL DB + sandboxed folder).

Mirrors the structure of ``text_sql_agent.py`` but is built PER SESSION from the live
resources in the registry, rather than as a singleton bound to a fixed database.
"""

import json
import os
from typing import Any, AsyncGenerator, Optional

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, PIIMiddleware
from langchain_community.utilities import SQLDatabase

from src.app.agents.data_agent.artifact_tools import make_artifact_tools
from src.app.agents.data_agent.compute_tools import make_compute_tools
from src.app.agents.data_agent.plan_tools import make_plan_tools
from src.app.agents.data_agent.tools import make_memory_tools
from src.app.agents.tools.search_tool import SearchAPI, get_search_tool
from src.app.core.common.config import settings
from src.app.core.common.graph_utils import process_messages
from src.app.core.common.logging import logger
from src.app.core.common.model.message import Message
from src.app.core.db.readonly import make_readonly_sql_tools
from src.app.core.learning import get_reflected_preferences
from src.app.core.llm.factory import active_model_name, create_chat_model
from src.app.core.sandbox.backend import ROOT_DIR_CONFIG_KEY, SKILLS_MOUNT, make_backend_factory
from src.app.core.memory.agent_memory_repository import AgentMemoryRepository
from src.app.core.memory.memory import bg_update_memory, get_relevant_memory
from src.app.core.middleware import (
    AgentContext,
    AgentPipeline,
    ErrorHandlingMiddleware,
    GuardrailMiddleware,
    LoggingMiddleware,
    build_invoke_config,
)
from src.app.agents.data_agent.document_tools import make_document_tools
from src.app.core.retrieval import make_retrieval_tools
from src.app.core.session.event_recorder import bg_record_tool_event
from src.app.core.session.event_repository import SessionEventRepository

# One stateless repository instance for recording episodic events off the streaming path.
_event_repo = SessionEventRepository()
# Experience memory (#23), read into the session-start briefing as the "work already done" index.
_memory_repo = AgentMemoryRepository()

# Bundled skills (SKILL.md files) shipped with the agent, always available via progressive
# disclosure regardless of whether the session has a granted folder. Mounted read-only at
# SKILLS_MOUNT by the per-session backend.
_BUNDLED_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


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
        root_dir: Optional[str] = None,
        user_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
        agent_id: Optional[int] = None,
        web_search: bool = False,
        memory_enabled: bool = True,
        skills_dir: Optional[str] = None,
        workspace_context: str = "",
        folder_writable: bool = False,
        session_id: Optional[str] = None,
    ):
        """Build a Data Agent over a session's sources, isolated to one user and agent.

        ``root_dir`` is the session's granted folder (or None). When set, the agent's file tools
        are served by a per-session ``FilesystemBackend`` rooted there; the path is threaded into
        each invocation's config so the backend factory resolves it per session. ``folder_writable``
        (a per-agent capability, off by default) allows the folder to be written; either way writes
        stay confined to ``root_dir``. ``session_id`` binds the artifact tool so generated
        deliverables are attributed to this session in the episodic log.
        """
        self.name = name
        self.user_id = user_id
        self.agent_id = agent_id
        self.memory_enabled = memory_enabled
        self.root_dir = root_dir
        self.agent = _create_data_deep_agent(
            db,
            root_dir,
            user_id,
            system_prompt,
            agent_id,
            web_search,
            memory_enabled,
            skills_dir,
            workspace_context,
            folder_writable,
            session_id,
        )
        self._pipeline = AgentPipeline(
            middlewares=[LoggingMiddleware(), ErrorHandlingMiddleware(), GuardrailMiddleware()],
            invoke_fn=self._core_invoke,
        )

    def _invoke_config(self, session_id: str, user_id: Optional[int]) -> dict:
        """Build the LangGraph invoke config, threading this session's granted root dir.

        The root dir goes under ``configurable`` (not ``metadata``) so the per-session backend
        factory can resolve it, without the host path leaking into Langfuse trace metadata.
        """
        config = build_invoke_config(session_id, user_id, self.name)
        if self.root_dir:
            config["configurable"][ROOT_DIR_CONFIG_KEY] = self.root_dir
        # A legit multi-deliverable turn can take ~25-30 tool calls (≈2 graph steps each), which
        # exceeds the shared default recursion limit and would crash mid-task with
        # GraphRecursionError. Raise it above the model-call cap so ModelCallLimitMiddleware (which
        # ends gracefully) is what stops a runaway, not a hard crash.
        config["recursion_limit"] = 2 * settings.ANTHROPIC_MODEL_CALL_LIMIT + 20
        return config

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
            config=self._invoke_config(session_id, user_id),
            agent_name=self.name,
            metadata={"model_name": active_model_name()},
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
        config = self._invoke_config(session_id, user_id)
        history = [{"role": m.role, "content": m.content} for m in messages]
        last_user = messages[-1].content if messages else ""

        # Auto-inject leading context (scoped to this agent): relevant long-term memory (mem0) and
        # the preferences learned by reflection (#20), so the agent respects what it has learned.
        leading: list[dict] = []
        if self.memory_enabled and user_id is not None and last_user:
            relevant = await get_relevant_memory(user_id, last_user, agent_id=self.agent_id)
            if relevant:
                leading.append(
                    {"role": "system", "content": f"Contexto do usuário (memória de longo prazo):\n{relevant}"}
                )
        if user_id is not None:
            prefs = await get_reflected_preferences(user_id, self.agent_id)
            if prefs:
                leading.append(
                    {
                        "role": "system",
                        "content": f"Preferências aprendidas deste usuário/agente (respeite-as):\n{prefs}",
                    }
                )
        # Inject the "work already done" index (#23) — recent experience-memory summaries — so a new
        # session knows what was already delivered/decided and doesn't redo it. Only the summaries
        # (tier 1); the agent calls ler_memoria(id) for the details when relevant.
        if user_id is not None:
            recent = await _memory_repo.list_recent(user_id, self.agent_id, limit=12)
            if recent:
                index = "\n".join(f"- [mem {m.id}] ({m.kind}) {m.summary}" for m in recent)
                leading.append(
                    {
                        "role": "system",
                        "content": (
                            "Trabalho já realizado neste agente (NÃO refaça o que já foi entregue; "
                            "use `ler_memoria(id)` para detalhes/caminhos antes de gerar algo de novo):\n"
                            + index
                        ),
                    }
                )
        payload_messages = [*leading, *history] if leading else history

        answer = ""
        async for event in self.agent.astream_events({"messages": payload_messages}, config=config, version="v2"):
            kind = event.get("event")
            if kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = _short(event.get("data", {}).get("input"))
                # A followable, one-line trace of what the agent is doing this turn.
                logger.info(
                    "agent_tool_start",
                    tool=tool_name,
                    session_id=session_id,
                    agent_id=self.agent_id,
                    tool_input=tool_input,
                )
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
                # Surface the plan as a live checklist (not just a raw JSON step) when the agent
                # calls write_todos.
                if tool_name == "write_todos":
                    todos = _parse_todos(event.get("data", {}).get("input"))
                    if todos:
                        yield {"type": "todos", "items": todos}
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                if hasattr(output, "content"):
                    output = output.content
                short_output = _short(output)
                logger.info(
                    "agent_tool_end",
                    tool=event.get("name", ""),
                    session_id=session_id,
                    output=short_output,
                )
                yield {"type": "tool_end", "name": event.get("name", ""), "output": short_output}
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                content = getattr(chunk, "content", None) if chunk is not None else None
                # Anthropic streams text deltas as a plain string and reasoning as a list of
                # {type: "thinking"/"text", ...} blocks. Route reasoning to a separate "thinking"
                # event (live "raciocínio" panel) and only the answer text into the memory answer.
                for kind_, text in _iter_stream_content(content):
                    if kind_ == "token":
                        answer += text
                    yield {"type": kind_, "content": text}

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


def _iter_stream_content(content: Any):
    """Yield ``(event_type, text)`` from a streamed chunk's content.

    Anthropic streams answer text as a plain string and reasoning as a list of content blocks
    (``{type: "thinking", thinking: "..."}`` for the summarized reasoning, ``{type: "text", ...}``
    for the answer, plus signature blocks to ignore). Maps thinking → ``"thinking"`` events and
    everything else that carries text → ``"token"`` events. Provider-agnostic: OpenAI's string
    content just yields tokens.
    """
    if isinstance(content, str):
        if content:
            yield ("token", content)
        return
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                if block:
                    yield ("token", str(block))
                continue
            if block.get("type") == "thinking":
                reasoning = block.get("thinking")
                if reasoning:
                    yield ("thinking", reasoning)
            elif block.get("type") == "text":
                text = block.get("text")
                if text:
                    yield ("token", text)
            # signature / other blocks carry no user-facing text — ignore.


def _parse_todos(raw: Any) -> Optional[list[dict[str, str]]]:
    """Extract the ``write_todos`` task list from a tool input, or None if it isn't parseable.

    Returns ``[{"content", "status"}, ...]`` so the UI can render a live checklist instead of a raw
    JSON blob. Tolerant of the input arriving as a dict or a JSON string.
    """
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    todos = data.get("todos")
    if not isinstance(todos, list):
        return None
    items: list[dict[str, str]] = []
    for todo in todos:
        if isinstance(todo, dict) and todo.get("content"):
            items.append({"content": str(todo["content"]), "status": str(todo.get("status", "pending"))})
    return items or None


# Tool-usage guidance the harness ALWAYS appends to a user's custom system prompt, so a
# user-authored persona never drops the mechanics the model needs to actually use its tools.
_HARNESS_CAPABILITIES = """

## Ferramentas desta sessão (não removível)
Conforme as fontes que o usuário conectou, você pode ter:
- **Banco SQL (somente leitura)** — ferramentas `list_tables`, `describe_tables`, `run_sql`
  (apenas `SELECT`/`WITH`/`EXPLAIN`/`SHOW`).
- **Uma pasta concedida** exposta **somente leitura** por ferramentas de arquivo (`ls`,
  `read_file`, `glob`, `grep`) montada em `/workspace`. `read_file` também extrai o texto de
  **PDF, Word (`.docx`) e Excel (`.xlsx`)** — leia o próprio arquivo, não tente decodificar bytes.
- **Busca semântica** — `buscar_documentos(consulta)` encontra trechos por significado nos
  documentos desta pasta/agente, cada resultado com a fonte. Prefira-a para localizar um trecho
  específico em documentos longos; use `read_file` para ler um arquivo inteiro (ou pequeno).
- **Catálogo e leitura de documentos** — `list_documents()` lista o acervo indexado (cada doc com
  `doc_id`, título, nº de páginas e camada de texto); `search_documents(query)` faz **busca literal**
  de um termo EXATO (número, artigo, data, valor, nome próprio) e devolve as coordenadas (doc_id,
  página, fólio); `read_document(doc_id, start_page, end_page)` lê um intervalo de páginas pelo
  `doc_id` (nunca pelo título); `read_page_image(doc_id, page)` renderiza a página como **imagem**
  para você VER — use como 1ª escolha quando o layout importa (tabela, coluna de valores, carimbo,
  assinatura) ou quando o doc é `ocr`/baixa confiança ou o texto sai ambíguo. Fluxo: `list_documents`
  (achar o `doc_id`) → localizar a página com `search_documents` (termo exato) **ou** `buscar_documentos`
  (conceito/paráfrase) → `read_document` (texto) ou `read_page_image` (imagem). Cada página traz o
  índice do PDF e o fólio impresso (com aviso de divergência).
- **Cálculo sobre arquivos de dados (CSV/TSV)** — `listar_dados()` mostra os arquivos da pasta como
  tabelas SQL (colunas + nº de linhas); `consultar_dados(sql)` roda **SQL de leitura (DuckDB)** e
  devolve o resultado **EXATO**. Use SEMPRE para somas, contagens, médias, rankings e cruzamentos
  sobre CSV/TSV — **NUNCA some/agregue linhas na mão**. Fluxo: `listar_dados` → `consultar_dados`.
- **Memória de longo prazo** — `buscar_memoria(consulta)`.
- **Aprovação de plano** — `propor_plano(titulo, passos)` propõe um plano e PAUSA para o usuário
  aprovar antes de executar. Use só antes de tarefas grandes, com muitos passos ou irreversíveis
  (não para perguntas simples). Após propor, aguarde a aprovação — você prossegue quando aprovado.
- **Geração de artefato** — `gerar_artefato(titulo, formato, secoes, ...)` para produzir um
  relatório em Word (`docx`) ou PowerPoint (`pptx`). Use quando o usuário pedir um relatório/
  documento/apresentação; inclua a `fonte` de cada item (tabela+consulta ou documento).
  **IMPORTANTE: para gerar `.docx` ou `.pptx` use SEMPRE `gerar_artefato`. NUNCA crie um arquivo
  `.docx`/`.pptx`/`.xlsx` com `write_file`** — `write_file` grava apenas texto e o arquivo Office
  sairia corrompido. `write_file` serve só para arquivos de texto (`.md`, `.txt`, `.csv`).

Regras: somente leitura em dados/banco; nunca modifique dados. Para perguntas sobre **arquivos**, use `ls`/`glob`
em `/workspace` e depois `read_file` para ler o conteúdo — funciona com texto, CSV, **PDF, Word e Excel**
(leia o arquivo antes de responder sobre ele). Para achar um trecho específico em documentos longos,
prefira `buscar_documentos`. Se um arquivo falhar ao ser lido, NÃO repita a mesma leitura em loop:
tente `buscar_documentos` ou diga que o documento não tem texto extraível. Nunca cite caminhos fora de `/workspace`.
Para perguntas sobre **documentos** (PDFs, leis, normas): `list_documents` → localizar a página com
`search_documents` (termo exato: artigo, número, data, nome) ou `buscar_documentos` (conceito) →
`read_document(doc_id, páginas)`. **Seja incansável: NUNCA conclua "não encontrei" após uma única
tentativa** — liste o acervo, tente variações do termo (sinônimos, número por extenso/algarismo) e
leia as páginas candidatas antes de desistir; só cite uma página depois de tê-la lido. (Acento e caixa
já são ignorados pelo `search_documents`, então não fique repetindo variações de acento.) Para perguntas de **banco**, SEMPRE `list_tables` →
`describe_tables` (das tabelas que vai usar) → `run_sql`, para a consulta nascer do schema real.
Executar a consulta é a validação: se `run_sql` retornar erro, corrija a partir das tabelas
disponíveis e execute de novo — **nunca invente tabelas ou colunas**, e não dê a resposta final
até a consulta rodar sem erro. Cada resultado de `run_sql` traz uma linha `[proveniência]`;
inclua essa fonte (tabela + consulta) na sua resposta. Seja conciso e cite os arquivos/tabelas usados.

**Cálculo (regra crítica):** NUNCA faça agregações numéricas — soma, contagem, média, ranking,
cruzamento — manualmente na resposta. Para dados em arquivo (CSV/TSV) use `consultar_dados` (SQL);
para dados no banco use `run_sql`. Deixe o SQL calcular e responda APENAS com o resultado final:
não mostre contas, somas parciais nem rascunho de raciocínio no texto da resposta.

**Plano vs. progresso (não duplique):** `propor_plano` e `write_todos` têm papéis diferentes —
use `propor_plano` UMA vez, no início, só para tarefas grandes/multi-etapas/irreversíveis, para o
usuário APROVAR antes de você começar. Use `write_todos` só para acompanhar o PROGRESSO durante a
execução (marcar cada passo como concluído). Depois de um plano aprovado, espelhe os passos
aprovados no `write_todos` uma vez e vá atualizando o status — não re-proponha o plano nem re-liste
tudo a cada passo. Para tarefas simples (poucos passos), não use nenhum dos dois.
"""


# Appended only when the granted folder is writable, so the model knows it may create/edit files
# there (overriding the default read-only file guidance). DB access stays strictly read-only.
_WRITABLE_FOLDER_NOTE = (
    "## Pasta gravável\n"
    "A pasta em `/workspace` está em modo LEITURA E ESCRITA nesta sessão: você PODE criar e editar "
    "arquivos nela com `write_file` e `edit_file` (ex.: gerar um relatório em `/workspace/…`). "
    "Toda escrita fica confinada a `/workspace` — nunca escreva fora dela. O banco de dados "
    "permanece somente leitura."
)


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
    root_dir: Optional[str],
    user_id: Optional[int],
    system_prompt: Optional[str] = None,
    agent_id: Optional[int] = None,
    web_search: bool = False,
    memory_enabled: bool = True,
    skills_dir: Optional[str] = None,
    workspace_context: str = "",
    folder_writable: bool = False,
    session_id: Optional[str] = None,
) -> Any:
    """Build the underlying Deep Agent with read-only SQL, memory tools, and optional folder.

    ``system_prompt`` sets the agent's persona; the harness capabilities guidance is always
    appended so tool usage survives. ``agent_id`` scopes the memory tools per agent.
    ``web_search`` adds a host-side web-search tool; ``memory_enabled`` gates the long-term
    memory tool. ``skills_dir`` (when set) is a directory of SKILL.md files the agent loads via
    progressive disclosure. ``workspace_context`` (when set) is a briefing of the attached sources,
    prepended so the agent is grounded from the first turn. When ``root_dir`` is set, the built-in
    file tools (ls/read_file/glob/grep) are served by a per-session ``FilesystemBackend`` rooted
    there — read-only unless ``folder_writable`` is True (then write_file/edit_file also work,
    still confined to the folder); the ``execute`` tool is never exposed (the backend is not a
    sandbox).
    """
    model = create_chat_model()
    tools = make_memory_tools(user_id, agent_id) if memory_enabled else []
    # Semantic search over this agent's ingested documents (#14), scoped to (user, agent).
    tools = tools + make_retrieval_tools(user_id, agent_id)
    # Document-layer tools: catalog the corpus (list_documents) and read explicit page ranges
    # (read_document) over the ingested manifest — complements the raw filesystem built-ins.
    tools = tools + make_document_tools(user_id, agent_id, session_id)
    # Artifact generation (#18): produces Word/PPTX and records an artifact_generated event that
    # feeds the success metrics (#21) and reflection (#20). Bound to this session; the deliverable
    # lands in the granted folder when it is writable, else a temp dir.
    tools = tools + make_artifact_tools(user_id, agent_id, session_id, root_dir, folder_writable)
    # Plan-approval (#19 gate): the agent can propose a plan and pause for the user's OK before large
    # or irreversible work.
    tools = tools + make_plan_tools(user_id, agent_id, session_id)
    # SQL compute over the folder's CSV/TSV files (#24), so exact aggregations are done by the engine
    # instead of the LLM summing rows by hand.
    if root_dir is not None:
        tools = tools + make_compute_tools(user_id, agent_id, root_dir, session_id)
    if db is not None:
        tools = tools + make_readonly_sql_tools(db)
    if web_search:
        # Runs host-side, alongside the read-only file tools.
        tools = tools + get_search_tool(SearchAPI.TAVILY)

    prompt = _compose_system_prompt(system_prompt)
    if workspace_context:
        prompt = f"{prompt}\n\n{workspace_context}"
    if root_dir is not None and folder_writable:
        # Override the default read-only file guidance: this agent may create/edit files in the
        # granted folder (still confined to /workspace).
        prompt = f"{prompt}\n{_WRITABLE_FOLDER_NOTE}"

    # create_deep_agent already bundles SummarizationMiddleware (context summarization near the
    # window) and AnthropicPromptCachingMiddleware (prompt caching, active once the model is
    # Anthropic) into its default stack — we add PII redaction and a hard model-call cap (safety net
    # so a runaway tool/planning loop ends gracefully instead of burning tokens).
    kwargs: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "system_prompt": prompt,
        "middleware": [
            PIIMiddleware("email"),
            ModelCallLimitMiddleware(run_limit=settings.ANTHROPIC_MODEL_CALL_LIMIT, exit_behavior="end"),
        ],
    }
    # Bundled skills mounted at SKILLS_MOUNT are always available; a caller-provided skills_dir is
    # appended (higher priority) for per-agent customization (progressive disclosure).
    kwargs["skills"] = [SKILLS_MOUNT] + ([skills_dir] if skills_dir is not None else [])
    # Route the built-in file tools: /workspace → the granted folder (when set), /skills → the
    # bundled read-only skills, everything else → the framework's ephemeral StateBackend scratch.
    kwargs["backend"] = make_backend_factory(
        root_dir or "", writable=folder_writable, skills_dir=_BUNDLED_SKILLS_DIR
    )

    return create_deep_agent(**kwargs)
