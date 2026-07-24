"""Deep Agent operating over a user's connected data sources (SQL DB + sandboxed folder).

Mirrors the structure of ``text_sql_agent.py`` but is built PER SESSION from the live
resources in the registry, rather than as a singleton bound to a fixed database.
"""

import json
import os
import re
from typing import Any, AsyncGenerator, Optional

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, PIIMiddleware
from langchain_community.utilities import SQLDatabase

from src.app.agents.data_agent.artifact_tools import make_artifact_tools
from src.app.agents.data_agent.version_tools import make_version_tools
from src.app.agents.data_agent.write_gate import ConfirmationGateBackend
from src.app.agents.data_agent.compute_tools import make_compute_tools
from src.app.agents.data_agent.completion_middleware import _DELIVERABLE_TOOLS as _DELIVERABLE_TOOL_NAMES
from src.app.agents.data_agent.completion_middleware import DeliverableCompletionMiddleware
from src.app.agents.data_agent.context_middleware import ToolResultCapMiddleware
from src.app.agents.data_agent.plan_tools import make_plan_tools
from src.app.agents.data_agent.subagents import make_deep_research_subagent_spec, make_user_sql_subagent
from src.app.agents.data_agent.tools import make_memory_tools
from src.app.agents.data_agent.workspace_memory import WorkspaceMemoryMiddleware
from src.app.core.checkpoint.checkpointer import get_checkpointer
from src.app.core.common.config import Environment, settings
from src.app.core.common.graph_utils import process_messages
from src.app.core.common.logging import logger
from src.app.core.common.model.message import Message
from src.app.core.learning import get_reflected_preferences
from src.app.core.llm.factory import active_model_name, create_chat_model
from src.app.core.sandbox.backend import (
    ROOT_DIR_CONFIG_KEY,
    SKILLS_MOUNT,
    USER_SKILLS_MOUNT,
    make_backend_factory,
)
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
from src.app.core.session.event_recorder import bg_record_delegation_event, bg_record_tool_event
from src.app.core.session.event_repository import SessionEventRepository
from src.app.core.session.message_repository import ChatMessageStepRepository

# One stateless repository instance for recording episodic events off the streaming path.
_event_repo = SessionEventRepository()
# Experience memory (#23), read into the session-start briefing as the "work already done" index.
_memory_repo = AgentMemoryRepository()
# Persisted tool steps, read into the intra-session "already read" ledger (P1 — stop the re-read spiral).
_step_repo = ChatMessageStepRepository()

# Read/compute tools whose past calls form the intra-session read-set: if the agent already ran one
# this conversation, the result is in the history — re-running it just re-reads and bloats context.
_READ_LEDGER_TOOLS = frozenset(
    {
        "read_file",
        "read_document",
        "read_page_image",
        "get_node_content",
        "get_document_structure",
        "search_documents",
        "consultar_dados",
        "listar_dados",
    }
)
# Cap the ledger so a very long session can't itself bloat the prefix it exists to shrink.
_LEDGER_MAX = 40
# Pull the meaningful target (file path / doc id) out of a step's serialized tool input.
_LEDGER_TARGET = re.compile(r"(?:file_path|doc_id|document_id|path|node_id)['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]")


async def get_data_agent_checkpointer() -> Optional[Any]:
    """Return the Postgres checkpointer for the data-agent, or None when it isn't available.

    The checkpointer is the agent's native working memory across a session's turns — with it, the
    graph keeps the full message/tool state per ``thread_id`` and stops re-reading files/queries
    every turn (the re-read spiral). It is Postgres-only, so:

    - In tests (``Environment.TEST``, in-memory SQLite) we short-circuit to None — never attempt a
      Postgres connection — so the suite exercises the unchanged stateless path and stays fast.
    - If the pool can't be reached (``get_checkpointer`` raises outside production), we degrade to
      None instead of failing agent construction.

    When None, the data-agent falls back to its per-turn stateless behavior (see ``_compose_payload``).
    """
    if settings.ENVIRONMENT == Environment.TEST:
        return None
    try:
        return await get_checkpointer()
    except Exception:
        # In production a checkpointer failure is unexpected (a real schema/permissions/pool fault) —
        # log it loudly. In dev/staging it is usually just "no Postgres running locally", so a warning
        # is enough. Either way, degrade to the stateless path rather than fail agent construction.
        if settings.ENVIRONMENT == Environment.PRODUCTION:
            logger.exception("data_agent_checkpointer_unavailable")
        else:
            logger.warning("data_agent_checkpointer_unavailable", exc_info=True)
        return None


def _ledger_label(name: str, raw: Optional[str]) -> str:
    """A compact one-line label for a read/compute step — its target file/doc, or an input preview."""
    text = (raw or "").replace("\n", " ").strip()
    match = _LEDGER_TARGET.search(text)
    if match:
        return f"{name}: {match.group(1)}"
    return f"{name}: {text[:70]}" if text else name


def _fold_context_into_user(leading: list[dict], last_user: str) -> dict:
    """Fold the volatile leading context into the new user turn (checkpointer-safe, no system msg).

    With a checkpointer the input is appended to the restored thread, where a ``system`` message would
    be non-consecutive and rejected by Anthropic — so the volatile context rides inside the user turn
    instead. Returns a bare user message when there's no leading context.
    """
    if not leading:
        return {"role": "user", "content": last_user}
    preamble = "\n\n".join(block["content"] for block in leading)
    return {"role": "user", "content": f"{preamble}\n\n---\n\n{last_user}"}


def _read_ledger(steps: list) -> str:
    """A deduped 'already read/computed this conversation' index from persisted tool steps.

    Only read-ish tools count (see ``_READ_LEDGER_TOOLS``) — writes/artifacts are not re-read.
    Deduped by label and capped at ``_LEDGER_MAX`` so the index stays a few hundred tokens even
    across a long session, keeping the agent aware of its read-set without re-sending file bodies.
    """
    seen: dict[str, None] = {}
    for step in steps:
        if step.name not in _READ_LEDGER_TOOLS:
            continue
        seen.setdefault(_ledger_label(step.name, step.input), None)
        if len(seen) >= _LEDGER_MAX:
            break
    return "\n".join(f"- {label}" for label in seen)


# Bundled skills (SKILL.md files) shipped with the agent, always available via progressive
# disclosure regardless of whether the session has a granted folder. Mounted read-only at
# SKILLS_MOUNT by the per-session backend.
BUNDLED_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


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
        sql_enabled: bool = False,
        deep_research_runnable: Optional[Any] = None,
        checkpointer: Optional[Any] = None,
    ):
        """Build a Data Agent over a session's sources, isolated to one user and agent.

        ``root_dir`` is the session's granted folder (or None). When set, the agent's file tools
        are served by a per-session ``FilesystemBackend`` rooted there; the path is threaded into
        each invocation's config so the backend factory resolves it per session. ``folder_writable``
        (a per-agent capability, off by default) allows the folder to be written; either way writes
        stay confined to ``root_dir``. ``session_id`` binds the artifact tool so generated
        deliverables are attributed to this session in the episodic log. ``sql_enabled`` (a per-agent
        capability, off by default) exposes the user's connected database through the isolated
        ``text_sql_agent`` subagent; when off, the connected DB is not queryable at all.
        """
        self.name = name
        self.user_id = user_id
        self.agent_id = agent_id
        self.memory_enabled = memory_enabled
        self.root_dir = root_dir
        # The session checkpointer (or None). When present, the graph keeps native working memory per
        # thread_id and _compose_payload sends only the new turn; when None, we stay stateless.
        self._checkpointer = checkpointer
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
            sql_enabled,
            deep_research_runnable,
            checkpointer,
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
        config["recursion_limit"] = 2 * settings.MODEL_CALL_LIMIT + 20
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

    async def _compose_payload(
        self,
        config: dict,
        leading: list[dict],
        history: list[dict],
        last_user: str,
    ) -> list[dict]:
        """Build the model input, adapting to whether this session has a checkpointer.

        Stateless (no checkpointer — tests/SQLite, or Postgres down): send the leading context as
        system messages + the server-rebuilt window, exactly as before. This is the unchanged,
        test-covered path — the system messages sit at the very front, so they stay consecutive.

        With a checkpointer, the graph *appends* the input to the restored thread. A ``system`` message
        appended after prior turns would sit mid-conversation, which Anthropic rejects ("multiple
        non-consecutive system messages"). So the volatile leading context is folded into the new
        **user** message instead of prepended as system messages — every sent message is then
        user/assistant/tool and the context still rides along each turn. The thread restores the prior
        turns (messages + tool results); an empty thread (a session created before the checkpointer
        existed) is seeded with its earlier turns so they aren't lost.
        """
        if self._checkpointer is None:
            return [*leading, *history] if leading else history

        new_user = _fold_context_into_user(leading, last_user)
        snapshot = await self.agent.aget_state(config)
        thread_has_history = bool(snapshot.values.get("messages")) if snapshot and snapshot.values else False
        if thread_has_history:
            return [new_user]
        prior = history[:-1] if history else []  # seed: earlier turns (user/assistant only), then the new turn
        return [*prior, new_user]

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
                            "use `ler_memoria(id)` para detalhes/caminhos antes de gerar algo de novo):\n" + index
                        ),
                    }
                )
        # Intra-session read-set (P1): what THIS conversation already read/computed. The results are in
        # the history above, so re-reading the same file or re-running the same query only re-inflates the
        # context — the failure mode in the degraded traces. Kept compact (labels only, capped).
        past_steps = await _step_repo.get_for_session(session_id)
        ledger = _read_ledger(past_steps)
        if ledger:
            leading.append(
                {
                    "role": "system",
                    "content": (
                        "Você JÁ leu/consultou o seguinte NESTA conversa (os resultados estão no histórico "
                        "acima) — NÃO releia o mesmo arquivo nem repita a mesma consulta; reuse o que já "
                        "apurou ou cite direto. Só releia se precisar de algo que ainda não viu:\n" + ledger
                    ),
                }
            )
        payload_messages = await self._compose_payload(config, leading, history, last_user)

        answer = ""
        # Depth of subagent delegation. A task() call runs a subagent whose own model tokens and
        # internal tool calls are its private work — streaming them would dump the subagent's raw
        # report into the parent's answer (mixing languages, ballooning the reply) and clutter the
        # timeline with its inner tools. While inside a delegation (depth > 0) we surface nothing but
        # the delegation's own boundary: its "Pesquisando na web…" card and, on return, its result.
        delegation_depth = 0
        # Count the parent's own model calls this turn. If it reaches the per-run cap, the deep agent's
        # ModelCallLimitMiddleware ends the turn and injects a limit message that is NOT streamed (it's
        # synthetic, not a model call) — so the turn would stop silently mid-task. We detect that and
        # stream a clear "continue" hint instead. Subagent calls run in their own graph and don't count.
        parent_model_calls = 0
        # Whether this turn ran any top-level tool and whether it ever called a deliverable-producing
        # tool. Used after the loop to detect the "worked, but ended with no file and no answer" turn
        # (the silent mid-plan stop) and surface a recoverable hint instead of an empty bubble.
        saw_planning = False
        deliverable_called = False
        async for event in self.agent.astream_events({"messages": payload_messages}, config=config, version="v2"):
            kind = event.get("event")
            if kind == "on_chat_model_start" and delegation_depth == 0:
                parent_model_calls += 1
            if kind == "on_tool_start":
                tool_name = event.get("name", "")
                if delegation_depth == 0:
                    if tool_name == "write_todos":
                        saw_planning = True
                    if tool_name in _DELIVERABLE_TOOL_NAMES:
                        deliverable_called = True
                raw_input = event.get("data", {}).get("input")
                tool_input = _short(raw_input)
                # For write_todos, stream/persist the plan as clean JSON (not the raw dict repr) so a
                # reopened conversation can rebuild the checklist instead of showing a JSON blob. The
                # live UI additionally gets a structured `todos` event below.
                todos = _parse_todos(raw_input) if tool_name == "write_todos" else None
                if todos is not None:
                    tool_input = json.dumps(todos, ensure_ascii=False)
                # A task() call delegates to a subagent — surface a human label ("Consultando o
                # banco…" / "Pesquisando na web…") so the timeline reads clearly and never looks
                # frozen during a long delegated run. subagent_type is None for non-task tools.
                display_name, subagent_type = _display_for_tool(tool_name, raw_input)
                # Only surface + audit top-level tools; a tool that fires while inside a delegation is
                # the subagent's own internal step and stays hidden from the parent timeline.
                if delegation_depth == 0:
                    # A followable, one-line trace of what the agent is doing this turn.
                    logger.info(
                        "agent_tool_start",
                        tool=tool_name,
                        subagent=subagent_type,
                        session_id=session_id,
                        agent_id=self.agent_id,
                        tool_input=tool_input,
                    )
                    # Audit trail (#10): record a delegation at its boundary (robust — does not rely on
                    # the subagent's nested events propagating); otherwise record document reads / SQL.
                    if subagent_type is not None:
                        bg_record_delegation_event(
                            _event_repo,
                            user_id=user_id,
                            agent_id=self.agent_id,
                            session_id=session_id,
                            subagent_type=subagent_type,
                            task=tool_input,
                        )
                    else:
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
                        "name": display_name,
                        "input": tool_input,
                    }
                    # Surface the plan as a live checklist (not just a raw JSON step) when the agent
                    # calls write_todos.
                    if todos:
                        yield {"type": "todos", "items": todos}
                if tool_name == "task":
                    delegation_depth += 1
            elif kind == "on_tool_end":
                name = event.get("name", "")
                if name == "task" and delegation_depth > 0:
                    delegation_depth -= 1
                # Surface the end only for a top-level tool (or the just-closed top-level delegation).
                if delegation_depth == 0:
                    output = event.get("data", {}).get("output")
                    if hasattr(output, "content"):
                        output = output.content
                    short_output = _short(output)
                    end_display_name, _ = _display_for_tool(name, event.get("data", {}).get("input"))
                    logger.info(
                        "agent_tool_end",
                        tool=name,
                        session_id=session_id,
                        output=short_output,
                    )
                    yield {"type": "tool_end", "name": end_display_name, "output": short_output}
            elif kind == "on_chat_model_stream":
                # A subagent's tokens are its private reasoning/report — never stream them as the
                # parent's answer; the parent gets the subagent's distilled result via the tool output.
                if delegation_depth > 0:
                    continue
                chunk = event.get("data", {}).get("chunk")
                content = getattr(chunk, "content", None) if chunk is not None else None
                # Anthropic streams text deltas as a plain string and reasoning as a list of
                # {type: "thinking"/"text", ...} blocks. Route reasoning to a separate "thinking"
                # event (live "raciocínio" panel) and only the answer text into the memory answer.
                for kind_, text in _iter_stream_content(content):
                    if kind_ == "token":
                        answer += text
                    yield {"type": kind_, "content": text}

        # A turn that ran tools but ended with no answer text and no deliverable is the silent mid-plan
        # stop the user sees as "frozen on the last task": the final model call (that would generate the
        # file) never completed — cap hit, a truncated tool call, or the stream being cut. Log a summary
        # so the exact cause is attributable next time, and surface a recoverable hint either way.
        hit_call_cap = parent_model_calls >= settings.MODEL_CALL_LIMIT
        # A *planned* turn (write_todos ran) that ended with no deliverable and no answer text is the
        # silent mid-plan stop — distinct from a plain delegation that simply returned nothing.
        turn_incomplete = saw_planning and not deliverable_called and not answer.strip()
        logger.info(
            "data_turn_summary",
            session_id=session_id,
            agent_id=self.agent_id,
            model_calls=parent_model_calls,
            answer_chars=len(answer),
            deliverable_called=deliverable_called,
            hit_call_cap=hit_call_cap,
            incomplete=turn_incomplete,
        )
        if hit_call_cap:
            hint = (
                "\n\n---\n\n_Cheguei ao limite de passos deste turno. Se ainda faltou algo (ex.: gerar o "
                'arquivo), envie **"continuar"** que eu retomo daqui — mantenho todo o contexto já '
                "apurado, sem refazer a análise._"
            )
            answer += hint
            yield {"type": "token", "content": hint}
        elif turn_incomplete:
            hint = (
                '_Reuni os dados mas não cheguei a gerar o arquivo neste turno. Envie **"continuar"** '
                "que eu finalizo o entregável a partir do que já apurei — sem refazer a análise._"
            )
            answer += hint
            yield {"type": "token", "content": hint}

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


# Human labels for a task() delegation, keyed by the subagent it targets, so the streamed timeline
# shows what is happening instead of a raw "task" step. Unknown subagents get a generic label.
_SUBAGENT_STREAM_LABELS = {
    "text_sql_agent": "Consultando o banco de dados…",
    "deep_research": "Pesquisando na web…",
}


def _display_for_tool(tool_name: str, raw_input: Any) -> tuple[str, Optional[str]]:
    """Map a tool event to ``(display_name, subagent_type)``.

    For a ``task()`` delegation, the display name is a human label and ``subagent_type`` is the
    targeted subagent (read from the tool input). For any other tool, the display name is the tool
    name and ``subagent_type`` is None.
    """
    if tool_name != "task":
        return tool_name, None
    subagent_type = raw_input.get("subagent_type") if isinstance(raw_input, dict) else None
    label = _SUBAGENT_STREAM_LABELS.get(subagent_type, "Executando subtarefa…")
    return label, subagent_type


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
- **Banco SQL do usuário (somente leitura)** — NÃO é uma ferramenta direta: delegue ao subagente
  `text_sql_agent` via `task`. Só está disponível quando o usuário conectou um banco e ativou a
  capacidade; se o subagente não constar entre os disponíveis, você não tem banco nesta sessão.
- **Uma pasta concedida** exposta **somente leitura** por ferramentas de arquivo (`ls`,
  `read_file`, `glob`, `grep`) montada em `/workspace`. `read_file` também extrai o texto de
  **PDF, Word (`.docx`) e Excel (`.xlsx`)** — leia o próprio arquivo, não tente decodificar bytes.
- **Catálogo e leitura de documentos** — `list_documents()` lista o acervo indexado (cada doc com
  `doc_id`, título, nº de páginas e camada de texto); `search_documents(query)` faz **busca literal**
  de um termo EXATO (número, artigo, data, valor, nome próprio) e devolve as coordenadas (doc_id,
  página, fólio); `read_document(doc_id, start_page, end_page)` lê um intervalo de páginas pelo
  `doc_id` (nunca pelo título); `read_page_image(doc_id, page)` renderiza a página como **imagem**
  para você VER — use como 1ª escolha quando o layout importa (tabela, coluna de valores, carimbo,
  assinatura) ou quando o doc é `ocr`/baixa confiança ou o texto sai ambíguo. Fluxo: `list_documents`
  (achar o `doc_id`) → `get_document_structure`/`search_documents` (achar a seção/página) →
  `read_document` (texto) ou `read_page_image` (imagem). Cada página traz o índice do PDF e o fólio
  impresso (com aviso de divergência).
- **Estrutura em árvore do documento** — `get_document_structure(doc_id)` mostra o ÍNDICE de seções
  (node_id, faixa de páginas/linhas, título); `get_node_content(doc_id, node_id)` lê o texto de UMA
  seção. Para um documento longo e estruturado (acórdão, contrato, norma), navegue pela árvore em vez
  de ler tudo: `list_documents` → `get_document_structure` → `get_node_content` na seção relevante.
- **Cálculo sobre arquivos de dados (CSV/TSV/Excel)** — `listar_dados()` mostra os arquivos da pasta
  como tabelas SQL (colunas + nº de linhas; cada aba do Excel é uma tabela); `consultar_dados(sql)`
  roda **SQL de leitura (DuckDB)** e devolve o resultado **EXATO**. Use SEMPRE para somas, contagens,
  médias, rankings e cruzamentos sobre CSV/TSV/Excel — **NUNCA some/agregue linhas na mão**. Para uma
  planilha `.xlsx`, prefira `consultar_dados` a ler o texto com `read_file`. Fluxo: `listar_dados` → `consultar_dados`.
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
(leia o arquivo antes de responder sobre ele). Para um documento longo e estruturado, **navegue pela
árvore** (`get_document_structure` → `get_node_content`). Se um arquivo falhar ao ser lido, NÃO
repita a mesma leitura em loop: tente outra seção ou diga que o documento não tem texto extraível.
Nunca cite caminhos fora de `/workspace`.
Para perguntas sobre **documentos** (PDFs, leis, normas): `list_documents` → `get_document_structure`
→ `get_node_content` na seção relevante. Para um termo EXATO (artigo, número, data, nome) use
`search_documents` e depois `read_document` na página encontrada.
**Seja incansável: NUNCA conclua "não encontrei" após uma única
tentativa** — liste o acervo, tente variações do termo (sinônimos, número por extenso/algarismo) e
leia as páginas candidatas antes de desistir; só cite uma página depois de tê-la lido. (Acento e caixa
já são ignorados pelo `search_documents`, então não fique repetindo variações de acento.) Para perguntas
sobre o **banco do usuário**, delegue ao subagente `text_sql_agent` via `task` (ele explora o schema,
escreve e executa o SQL somente-leitura e devolve o resultado com proveniência); inclua a fonte que ele
retornar na sua resposta. Seja conciso e cite os arquivos/tabelas usados.

**Cálculo (regra crítica):** NUNCA faça agregações numéricas — soma, contagem, média, ranking,
cruzamento — manualmente na resposta. Para dados em arquivo (CSV/TSV) use `consultar_dados` (SQL);
para dados no banco do usuário, delegue ao subagente `text_sql_agent`. Deixe o SQL calcular e responda
APENAS com o resultado final: não mostre contas, somas parciais nem rascunho de raciocínio no texto da resposta.

**Plano vs. progresso (não duplique):** `propor_plano` e `write_todos` têm papéis diferentes —
use `propor_plano` UMA vez, no início, só para tarefas grandes/multi-etapas/irreversíveis, para o
usuário APROVAR antes de você começar. Use `write_todos` só para acompanhar o PROGRESSO durante a
execução (marcar cada passo como concluído). Depois de um plano aprovado, espelhe os passos
aprovados no `write_todos` uma vez e vá atualizando o status — não re-proponha o plano nem re-liste
tudo a cada passo. Para tarefas simples (poucos passos), não use nenhum dos dois.
"""


# Appended only when the session has a connected DB AND the sql capability is on, so the guidance
# to delegate DB questions appears exactly when the text_sql_agent subagent actually exists.
_SQL_SUBAGENT_NOTE = (
    "## Banco de dados do usuário (via subagente)\n"
    "Há um banco SQL conectado nesta sessão. Ele NÃO é uma ferramenta direta sua: para qualquer "
    "pergunta que dependa dos dados do banco (contagens, somas, médias, rankings, listagens, "
    "cruzamentos), delegue ao subagente `text_sql_agent` chamando `task` com a pergunta COMPLETA "
    "em linguagem natural e dizendo o que ele deve devolver. Ele é somente-leitura, explora o "
    "schema, escreve e executa o SQL e retorna o resultado com a proveniência (tabelas + consulta) "
    "— inclua essa fonte na sua resposta. NUNCA agregue números na mão: deixe o subagente calcular."
)


# Appended only when web search is on AND the deep_research subagent compiled, so the guidance to
# delegate web research appears exactly when the subagent actually exists.
_WEB_RESEARCH_NOTE = (
    "## Pesquisa na web (via subagente)\n"
    "Você NÃO tem busca web direta. Para perguntas que dependem de informação EXTERNA/atual (não "
    "presente nos documentos nem no banco), delegue ao subagente `deep_research` chamando `task` "
    "com a pergunta COMPLETA e o que deve constar no relatório. Ele pesquisa múltiplas fontes e "
    "devolve um relatório com citações — inclua as fontes na sua resposta. É mais lento (roda "
    "pesquisadores em paralelo): use só quando realmente precisar da web, não para o que já sabe."
)


# Appended only when the granted folder is writable, so the model knows it may create/edit files
# there (overriding the default read-only file guidance). DB access stays strictly read-only.
_WRITABLE_FOLDER_NOTE = (
    "## Pasta gravável\n"
    "A pasta em `/workspace` está em modo LEITURA E ESCRITA nesta sessão: você PODE criar e editar "
    "arquivos nela com `write_file` e `edit_file` (ex.: gerar um relatório em `/workspace/…`). "
    "Toda escrita fica confinada a `/workspace` — nunca escreva fora dela. O banco de dados "
    "permanece somente leitura. Criar um arquivo novo funciona na hora; sobrescrever um arquivo "
    "JÁ EXISTENTE fica pendente de confirmação do usuário (a chamada retorna "
    "'pending_confirmation' — isso é esperado, não repita a chamada, ela será aplicada quando o "
    "usuário confirmar). Toda sobrescrita confirmada fica salva como versão recuperável: use "
    "`listar_versoes` pra ver o histórico de um arquivo, ou `desfazer_ultima_alteracao` pra "
    "restaurar a versão anterior."
)


def _build_subagents(
    db: Optional[SQLDatabase],
    sql_enabled: bool,
    web_search: bool = False,
    deep_research_runnable: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Subagents the Data Agent delegates to via ``task()``.

    - The user's database is reached only through the read-only ``text_sql_agent`` — and only when
      a database is connected AND the ``sql`` capability is on.
    - Web research is reached only through the ``deep_research`` subagent — and only when the
      ``web_search`` capability is on AND its runnable compiled (``deep_research_runnable`` is set;
      it is None when ``OPENAI_API_KEY`` is missing).

    With the gating condition false, that capability is not reachable by any path.
    """
    subagents: list[dict[str, Any]] = []
    if db is not None and sql_enabled:
        subagents.append(make_user_sql_subagent(db))
    if web_search and deep_research_runnable is not None:
        subagents.append(make_deep_research_subagent_spec(deep_research_runnable))
    return subagents


def _compose_system_prompt(system_prompt: Optional[str]) -> str:
    """Return the effective system prompt.

    With no user prompt, use the bundled default (which already documents the tools). With a
    user-authored prompt, keep it as the persona but append the non-removable capabilities
    guidance so the model still knows how to use the filesystem/memory tools (DB access is
    delegated to the text_sql_agent subagent, documented separately when enabled).
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
    sql_enabled: bool = False,
    deep_research_runnable: Optional[Any] = None,
    checkpointer: Optional[Any] = None,
) -> Any:
    """Build the underlying Deep Agent with memory tools, an optional folder, and subagents.

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

    The user's connected database is NOT a direct tool: when ``db`` is set and ``sql_enabled`` is
    True, it is reached only through the isolated read-only ``text_sql_agent`` subagent, so the
    schema-exploration/query loop stays out of this agent's context. With ``sql_enabled`` False the
    connected DB is not queryable at all. Likewise, web search is not a direct tool: when
    ``web_search`` is True and ``deep_research_runnable`` is provided, web questions are delegated
    to the isolated ``deep_research`` subagent (which replaces the old direct Tavily tool).
    """
    model = create_chat_model()
    tools = make_memory_tools(user_id, agent_id) if memory_enabled else []
    # Document-layer tools over the ingested manifest (vectorless): catalog (list_documents), the
    # structure tree (get_document_structure/get_node_content), literal search + page reads — plus
    # the raw filesystem built-ins. No embeddings/chunks: content is served from the manifest.
    tools = tools + make_document_tools(user_id, agent_id, session_id)
    # Artifact generation (#18): produces Word/PPTX and records an artifact_generated event that
    # feeds the success metrics (#21) and reflection (#20). Bound to this session; the deliverable
    # lands in the granted folder when it is writable, else a temp dir.
    tools = tools + make_artifact_tools(user_id, agent_id, session_id, root_dir, folder_writable)
    # Version history + undo (#55) for a writable folder: every overwrite is snapshotted by the
    # VersioningBackend wrapping the folder backend (see sandbox/backend.py); these tools let the
    # agent list that history and restore the most recent prior version of a file.
    tools = tools + make_version_tools(root_dir, folder_writable)
    # Plan-approval (#19 gate): the agent can propose a plan and pause for the user's OK before large
    # or irreversible work.
    tools = tools + make_plan_tools(user_id, agent_id, session_id)
    # SQL compute over the folder's CSV/TSV files (#24), so exact aggregations are done by the engine
    # instead of the LLM summing rows by hand.
    if root_dir is not None:
        tools = tools + make_compute_tools(user_id, agent_id, root_dir, session_id)

    # Subagents delegated via task() — the noisy/expensive work runs in an isolated context and
    # only the distilled result returns. The user's DB is reached only through the read-only
    # text_sql_agent (gated by sql_enabled); web research is reached only through the deep_research
    # subagent (gated by web_search), which replaces the old direct Tavily tool.
    subagents = _build_subagents(db, sql_enabled, web_search, deep_research_runnable)

    prompt = _compose_system_prompt(system_prompt)
    if workspace_context:
        prompt = f"{prompt}\n\n{workspace_context}"
    if db is not None and sql_enabled:
        # Tell the agent to route DB questions to the subagent (its description + the task tool are
        # auto-injected by SubAgentMiddleware, but this reinforces when and how to delegate).
        prompt = f"{prompt}\n{_SQL_SUBAGENT_NOTE}"
    if web_search and deep_research_runnable is not None:
        prompt = f"{prompt}\n{_WEB_RESEARCH_NOTE}"
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
        "subagents": subagents,
        "middleware": [
            # Cap a single oversized tool result to a preview before it reaches the model (the full
            # result stays in state) — prevents a one-turn context blowup ahead of the summarizer.
            ToolResultCapMiddleware(),
            PIIMiddleware("email"),
            # Keep weaker instruction-followers from ending the turn mid-plan: if the model stops with
            # the deliverable ungenerated and the plan still incomplete, jump back to the model with a
            # firm "finish it" nudge (bounded). Model-agnostic; the call cap below is the backstop.
            DeliverableCompletionMiddleware(),
            ModelCallLimitMiddleware(run_limit=settings.MODEL_CALL_LIMIT, exit_behavior="end"),
        ],
    }
    # Bundled skills mounted at SKILLS_MOUNT are always available; a caller-provided skills_dir
    # (the user's approved, materialized skills) is appended via its own mount (higher priority)
    # for per-agent customization (progressive disclosure). Both are virtual routes resolved by
    # the backend below — SkillsMiddleware has no direct filesystem access, so the raw temp-dir
    # path itself must never be handed to `skills=[...]` (it would silently resolve to nothing).
    kwargs["skills"] = [SKILLS_MOUNT] + ([USER_SKILLS_MOUNT] if skills_dir is not None else [])
    # Route the built-in file tools: /workspace → the granted folder (when set), /skills → the
    # bundled read-only skills, /skills/user → the caller's materialized skills (when set),
    # everything else → the framework's ephemeral StateBackend scratch. A writable folder's
    # overwrites are gated behind explicit confirmation (#57) — only when we have a user/session to
    # attribute the pending action to.
    workspace_wrapper = None
    if folder_writable and user_id is not None and session_id:

        def workspace_wrapper(backend: Any, resolved_root_dir: str) -> Any:
            return ConfirmationGateBackend(backend, resolved_root_dir, user_id, session_id)

    kwargs["backend"] = make_backend_factory(
        root_dir or "",
        writable=folder_writable,
        skills_dir=BUNDLED_SKILLS_DIR,
        user_skills_dir=skills_dir,
        workspace_wrapper=workspace_wrapper,
    )
    # Read-only AGENTS.md briefing for the granted folder (RF-24/25/26, agents.md convention):
    # reuses the same backend/route as the file tools above, so no new mount is needed. Subclassed
    # to drop deepagents' stock self-editing guidance — see workspace_memory.py for why.
    if root_dir is not None:
        kwargs["middleware"].append(
            WorkspaceMemoryMiddleware(
                backend=kwargs["backend"],
                sources=[f"{settings.SANDBOX_MOUNT_PATH.rstrip('/')}/AGENTS.md"],
            )
        )
    # When a checkpointer is available (Postgres), the graph persists per-thread working memory so the
    # agent keeps prior turns' messages/tool results without them being re-sent each turn. None keeps
    # the deep agent stateless (tests/SQLite, or Postgres down).
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer

    return create_deep_agent(**kwargs)
