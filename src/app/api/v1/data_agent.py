"""Data Agent API: connect a read-only database, grant a folder (phase 2), and query.

Credentials are received over the authenticated session, used to build an in-memory
connection held by the per-session registry, and never persisted or logged.
"""

import asyncio
import json
import os
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import FileResponse, StreamingResponse

from src.app.agents.data_agent import build_data_agent
from src.app.agents.data_agent.context import build_workspace_context
from src.app.agents.data_agent.subagents import get_deep_research_subagent_runnable
from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_session
from src.app.api.v1.dtos.data_agent import (
    ChatHistoryResponse,
    ConnectDbRequest,
    ConnectDbResponse,
    DataQueryRequest,
    DataQueryResponse,
    DisconnectResponse,
    GrantFolderRequest,
    GrantFolderResponse,
    HistoryMessage,
    HistoryStep,
    SourceStatusResponse,
)
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.common.model.message import Message
from src.app.core.hitl.pending_model import PendingActionStatus
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.ingestion.trigger import (
    is_ingesting,
    run_folder_ingestion_if_changed,
    schedule_folder_ingestion,
)
from src.app.core.db.connect import build_db_url, connect_readonly
from src.app.core.security import decrypt
from src.app.core.sandbox import registry
from src.app.core.sandbox.paths import is_within_allowed_roots, validate_grantable_folder
from src.app.core.sandbox.registry import SessionResources
from src.app.core.session.message_model import ChatMessageRole
from src.app.core.session.session_model import Session
from src.app.core.skill.materialize import materialize_skills
from src.app.init import (
    agent_repository,
    chat_message_repository,
    chat_message_step_repository,
    pending_action_repository,
    session_repository,
    skill_repository,
)

_ARTIFACT_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

router = APIRouter()


@router.post("/connect-db", response_model=ConnectDbResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_connect"][0])
async def connect_db(
    request: Request,
    body: ConnectDbRequest,
    session: Session = Depends(get_current_session),
) -> ConnectDbResponse:
    """Connect a read-only database for this session (credentials kept in memory only)."""
    # Note: only non-sensitive fields are logged; password is never passed to the logger.
    logger.info(
        "db_connect_requested",
        session_id=session.id,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
    )

    url = build_db_url(
        body.driver, body.username, body.password.get_secret_value(), body.host, body.port, body.database, body.sslmode
    )
    try:
        db = await connect_readonly(url)
        tables = await asyncio.to_thread(db.get_usable_table_names)
    except Exception as e:
        logger.warning("db_connect_failed", session_id=session.id, error_type=type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail="Falha ao conectar ao banco. Verifique host, porta, credenciais e conectividade.",
        )

    await registry.set_database(session.id, db, db.dialect)
    logger.info("db_connected", session_id=session.id, dialect=db.dialect, table_count=len(tables))
    return ConnectDbResponse(connected=True, dialect=db.dialect, table_count=len(tables))


@router.post("/grant-folder", response_model=GrantFolderResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_connect"][0])
async def grant_folder(
    request: Request,
    body: GrantFolderRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_current_session),
) -> GrantFolderResponse:
    """Grant read-only access to a host folder, served by the session's FilesystemBackend."""
    # Security: folder must exist and resolve under a configured allow-listed root.
    path = validate_grantable_folder(body.path)

    logger.info("folder_grant_requested", session_id=session.id, folder=path)
    # No external resource to create — the read-only FilesystemBackend is resolved per
    # invocation from the granted path (no docker run on the first-response path).
    await registry.set_folder(session.id, path)
    # Ingest the folder in the background (incremental) so semantic search has a corpus. Scoped to
    # the same (user, agent) the retrieval tool queries, so the two always match.
    schedule_folder_ingestion(background, session.user_id, session.agent_id, path)
    logger.info("folder_granted", session_id=session.id, folder=path)
    return GrantFolderResponse(granted=True, folder=path)


@router.post("/query", response_model=DataQueryResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def query_sources(
    request: Request,
    body: DataQueryRequest,
    session: Session = Depends(get_current_session),
) -> DataQueryResponse:
    """Ask the Data Agent a question over the session's connected sources."""
    res = await registry.get(session.id)
    if res is None or not res.has_source:
        raise HTTPException(
            status_code=400,
            detail="Nenhuma fonte conectada. Conecte um banco (ou autorize uma pasta) primeiro.",
        )

    try:
        if res.agent is None:
            res.agent = await _build_agent_for_session(res, session)
        messages = [Message(role="user", content=body.query)]
        result = await res.agent.agent_invoke(messages, session.id, user_id=session.user_id)
        logger.info("data_query_processed", session_id=session.id)
        return DataQueryResponse(messages=result)
    except HTTPException:
        raise
    except Exception:
        logger.exception("data_query_failed", session_id=session.id)
        raise HTTPException(status_code=500, detail="Erro ao processar a consulta.")


async def _sync_corpus_in_background(user_id: int, agent_id: Optional[int], folder: str) -> None:
    """Keep the corpus + map fresh on every session, in the background (#23).

    An incremental ``sync_folder`` runs on each session build so files added or removed from the
    folder between sessions are picked up deterministically (new files ingested + described, removed
    files soft-deleted) — without the agent having to notice a discrepancy. It also self-heals a
    wiped/un-embedded corpus (the incremental sync re-ingests anything missing its chunks). The
    session's briefing reflects the *previous* sync (this one runs after the response starts), so a
    just-made change shows up on the next session — an acceptable one-session lag for not blocking
    the turn. Best-effort and idempotent: the ingestion trigger's in-flight guard prevents concurrent
    runs, and any failure is swallowed.
    """
    try:
        asyncio.create_task(run_folder_ingestion_if_changed(user_id, agent_id, folder))
    except Exception:  # noqa: BLE001 - a background sync must never break the chat
        logger.exception("corpus_sync_failed", user_id=user_id, agent_id=agent_id)


async def _ensure_agent_folder(res: SessionResources, session: Session, folder: str) -> None:
    """Bind the agent's configured folder into this session, if not already bound.

    Re-validates the folder against the allow-list on every use, so tightening
    ``SANDBOX_ALLOWED_ROOTS`` immediately revokes a stale binding. Degrades gracefully:
    a disabled feature or an invalid path just leaves the agent without file tools rather
    than failing the whole chat.
    """
    if res.folder is not None:
        return  # already bound for this session
    if not settings.SANDBOX_ENABLED or not settings.SANDBOX_ALLOWED_ROOTS:
        return
    abspath = os.path.abspath(folder)
    if not os.path.isdir(abspath) or not is_within_allowed_roots(abspath, settings.SANDBOX_ALLOWED_ROOTS):
        logger.warning("agent_folder_binding_invalid", session_id=session.id, folder=abspath)
        return
    await registry.set_folder(session.id, abspath)
    logger.info("agent_folder_bound", session_id=session.id, folder=abspath)


async def _ensure_agent_database(res: SessionResources, session: Session, db_conf: dict) -> None:
    """Materialize the agent's bound database into this session, if not already connected.

    The password is decrypted in memory only; if it was never persisted (no encryption key at
    bind time) or the connection fails, the agent simply runs without SQL tools rather than
    failing the chat.
    """
    if res.db is not None:
        return
    token = db_conf.get("password_encrypted")
    if not token:
        return  # password was not persisted (secure fallback) — nothing to connect with
    try:
        password = decrypt(token)
        url = build_db_url(
            db_conf["driver"],
            db_conf["username"],
            password,
            db_conf["host"],
            int(db_conf["port"]),
            db_conf["database"],
            db_conf.get("sslmode"),
        )
        db = await connect_readonly(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_database_materialize_failed", session_id=session.id, error_type=type(e).__name__)
        return
    await registry.set_database(session.id, db, db.dialect)
    logger.info("agent_database_materialized", session_id=session.id, dialect=db.dialect)


async def _build_agent_for_session(res: SessionResources, session: Session):
    """Build a Data Agent from the session's live sources and its bound agent config.

    When the session is bound to an agent, the agent's system prompt is applied, its bound
    folder and database are materialized, capability toggles are honored, and its id scopes
    long-term memory (per-agent isolation). Works with zero sources.
    """
    system_prompt = None
    name = "Data Agent"
    web_search = False
    sql_enabled = False
    memory_enabled = True
    skills_dir = None
    folder = None
    folder_writable = False
    if session.agent_id is not None:
        # Isolation choke point (#11): resolve the bound agent through the ownership filter, so a
        # session can never materialize another user's folder, DB password, or skills. A non-owned
        # (or absent) agent falls through to plain defaults — fail-closed, no foreign resources.
        agent = await agent_repository.get_owned_agent(session.agent_id, session.user_id)
        if agent is None:
            logger.warning(
                "session_agent_ownership_mismatch",
                session_id=session.id,
                user_id=session.user_id,
                agent_id=session.agent_id,
            )
        if agent is not None:
            config = agent.config or {}
            system_prompt = agent.system_prompt or None
            name = agent.name or name
            web_search = bool(config.get("web_search", False))
            sql_enabled = bool(config.get("sql", False))
            # Treat a missing OR null memory flag as enabled (default on).
            memory_enabled = config.get("memory") is not False
            folder = config.get("folder")
            if folder:
                # Read-write access is opt-in per agent (default read-only).
                folder_writable = bool(config.get("folder_writable", False))
                await _ensure_agent_folder(res, session, folder)
                if res.folder:
                    # Keep the corpus + map fresh: an incremental background sync catches files
                    # added/removed since last session (and self-heals a wiped corpus).
                    await _sync_corpus_in_background(session.user_id, session.agent_id, res.folder)
            if config.get("database"):
                await _ensure_agent_database(res, session, config["database"])
            skills_dir = await _materialize_agent_skills(session.agent_id, agent.user_id, config.get("skills"))
    # Prime the agent with a briefing of its attached sources (the indexed document manifest + DB
    # schema) so it is grounded from the first turn — and so the briefing matches exactly what the
    # document tools can search (never a disk file the tools can't reach).
    docs = await IngestedFileRepository().list_all(session.user_id, session.agent_id) if res.folder else None
    workspace_context = build_workspace_context(res.folder, res.db, docs)
    # Web search is delegated to the deep_research subagent (replaces the old direct Tavily tool):
    # compile the session-independent graph once (off the per-session path) only when web search is
    # on. None when OPENAI_API_KEY is absent, in which case the subagent is simply not registered.
    deep_research_runnable = await get_deep_research_subagent_runnable() if web_search else None
    return build_data_agent(
        res,
        user_id=session.user_id,
        system_prompt=system_prompt,
        agent_id=session.agent_id,
        name=name,
        web_search=web_search,
        memory_enabled=memory_enabled,
        skills_dir=skills_dir,
        workspace_context=workspace_context,
        folder_writable=folder_writable,
        session_id=session.id,
        sql_enabled=sql_enabled,
        deep_research_runnable=deep_research_runnable,
    )


async def _materialize_agent_skills(agent_id: int, owner_id: int, skill_ids) -> Optional[str]:
    """Write the agent's attached skills to a SKILL.md directory, or None if none.

    Only skills owned by the agent's owner are materialized (defense in depth against a stale
    or tampered id list referencing another user's skill).
    """
    if not skill_ids:
        return None
    skills = await skill_repository.get_skills_by_ids(list(skill_ids))
    # Only the owner's APPROVED skills load — draft/in_review never reach the agent (#17).
    loadable = [s for s in skills if s.user_id == owner_id and s.status == "approved"]
    return materialize_skills(agent_id, loadable)


async def _get_or_build_agent(session: Session):
    """Return the session's Data Agent, building it if needed (works with zero sources)."""
    res = await registry.ensure(session.id)
    if res.agent is None:
        res.agent = await _build_agent_for_session(res, session)
    return res.agent


# How many recent persisted messages to replay to the agent for immediate conversational context.
# Older context comes from long-term memory, so this stays bounded rather than the whole history.
_HISTORY_WINDOW = 20


def _require_session(session_id: str, session: Session) -> None:
    """Guard: the path session must be the caller's authenticated session (traceability + safety)."""
    if session_id != session.id:
        logger.warning("session_path_mismatch", path_session_id=session_id, token_session_id=session.id)
        raise HTTPException(status_code=403, detail="Sessão não corresponde ao token.")


def _agent_messages(history, query: str) -> list[Message]:
    """The recent conversation window the agent sees: prior non-empty turns + the new message.

    Only a bounded window is replayed (older context is carried by long-term memory), and empty
    tool-only turns are skipped since ``Message.content`` must be non-empty.
    """
    window = [Message(role=row.role, content=row.content) for row in history if row.content.strip()]
    return window + [Message(role="user", content=query)]


async def _persist_user_message(session: Session, query: str) -> None:
    """Persist the user's new message and name the session from it on the first turn."""
    await chat_message_repository.add_message(session.id, session.user_id, ChatMessageRole.USER, query)
    if not session.name:
        name = query.strip().splitlines()[0][:60] or "Nova conversa"
        await session_repository.update_session_name(session.id, name)


async def _persist_answer(session: Session, answer: str, steps: list[dict]) -> None:
    """Persist the assistant's reply and its tool-activity steps for this turn.

    ``answer`` is the concatenation of the same ``token`` events the client renders into the
    assistant bubble (and that ``astream_query_events`` already accumulates for long-term memory), so
    the stored message is exactly what the user saw. ``steps`` is the turn's tool activity, persisted
    alongside so a reopened conversation shows the same "buscando/gerando/…" trail and timeline.

    A turn with activity but no final text (e.g. the agent only parked a HITL action) is still
    persisted with empty content so its tool trail survives a reload; a turn with neither is skipped.
    """
    text = answer.strip()
    if not text and not steps:
        return
    message = await chat_message_repository.add_message(session.id, session.user_id, ChatMessageRole.ASSISTANT, text)
    await chat_message_step_repository.add_steps(session.id, message.id, steps)


def _close_step(steps: list[dict], name: str, output) -> None:
    """Attach ``output`` to the most recent still-open step of ``name`` (mirrors the client)."""
    for step in reversed(steps):
        if step["name"] == name and step["output"] is None:
            step["output"] = output
            return


def _hitl_event(action) -> dict:
    """Shape a parked action into an inline ``hitl_request`` SSE event for the chat."""
    payload = action.payload or {}
    spec = payload.get("spec") or {}
    return {
        "type": "hitl_request",
        "id": action.id,
        "action_type": action.action_type,
        # export_artifact carries the title in its spec; approve_plan carries it at the top level.
        "title": spec.get("title") or payload.get("title") or "Ação pendente",
        "format": payload.get("fmt"),
    }


async def _session_pending_ids(session: Session) -> set[int]:
    """Ids of this session's currently-pending actions (used to diff a turn's new requests)."""
    pending = await pending_action_repository.list_pending(session.user_id)
    return {a.id for a in pending if a.session_id == session.id}


async def _new_hitl_events(session: Session, known_ids: set[int]) -> list[dict]:
    """Events for actions this turn parked for approval (pending, this session, not seen before)."""
    pending = await pending_action_repository.list_pending(session.user_id)
    return [_hitl_event(a) for a in pending if a.session_id == session.id and a.id not in known_ids]


@router.post("/{session_id}/query/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def query_stream(
    request: Request,
    session_id: str,
    body: DataQueryRequest,
    session: Session = Depends(get_current_session),
) -> StreamingResponse:
    """Stream the Data Agent's work (tool calls, reasoning, tokens) as SSE events.

    The client sends only the new message; the server rebuilds a bounded recent window from the
    persisted history for immediate coherence, while long-term memory and learned preferences carry
    the older/cross-session context.
    """
    _require_session(session_id, session)
    agent = await _get_or_build_agent(session)
    logger.info("data_stream_started", session_id=session.id)

    async def event_generator():
        answer_parts: list[str] = []
        steps: list[dict] = []
        try:
            known_ids = await _session_pending_ids(session)
            # Rebuild the recent context from our own persisted history (the client sends only the
            # new message); older turns are covered by the agent's long-term memory.
            history = await chat_message_repository.get_messages(session.id, limit=_HISTORY_WINDOW)
            agent_messages = _agent_messages(history, body.query)
            await _persist_user_message(session, body.query)
            async for ev in agent.astream_query_events(agent_messages, session.id, session.user_id):
                etype = ev.get("type")
                if etype == "token":
                    answer_parts.append(ev.get("content", ""))
                elif etype == "tool_start":
                    steps.append({"name": ev.get("name", ""), "input": ev.get("input"), "output": None})
                elif etype == "tool_end":
                    _close_step(steps, ev.get("name", ""), ev.get("output"))
                yield f"data: {json.dumps(ev)}\n\n"
            # Surface any approval the agent just parked as an inline card, before closing the turn.
            for hitl_ev in await _new_hitl_events(session, known_ids):
                yield f"data: {json.dumps(hitl_ev)}\n\n"
            await _persist_answer(session, "".join(answer_parts), steps)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception:
            logger.exception("data_stream_failed", session_id=session.id)
            yield f"data: {json.dumps({'type': 'error', 'content': 'Erro ao processar a consulta.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{session_id}/messages", response_model=ChatHistoryResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def messages(
    request: Request,
    session_id: str,
    session: Session = Depends(get_current_session),
) -> ChatHistoryResponse:
    """Return this session's persisted conversation (oldest first), with each turn's tool activity."""
    _require_session(session_id, session)
    rows = await chat_message_repository.get_messages(session.id)
    steps = await chat_message_step_repository.get_for_session(session.id)
    by_message: dict[int, list[HistoryStep]] = {}
    for step in steps:
        by_message.setdefault(step.message_id, []).append(
            HistoryStep(name=step.name, input=step.input, output=step.output)
        )
    return ChatHistoryResponse(
        messages=[HistoryMessage(role=row.role, content=row.content, steps=by_message.get(row.id, [])) for row in rows]
    )


@router.get("/{session_id}/artifacts/{action_id}/download")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def download_artifact(
    request: Request,
    session_id: str,
    action_id: int,
    session: Session = Depends(get_current_session),
) -> FileResponse:
    """Download a confirmed artifact. Owner-scoped; the file exists only after approval."""
    _require_session(session_id, session)
    action = await pending_action_repository.get(action_id)
    if action is None or action.action_type != "export_artifact" or action.session_id != session_id:
        raise HTTPException(status_code=404, detail="Artefato não encontrado.")
    if action.user_id != session.user_id:
        logger.warning("artifact_download_denied", action_id=action_id, user_id=session.user_id)
        raise HTTPException(status_code=403, detail="Artefato pertence a outro usuário.")
    if action.status != PendingActionStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Artefato ainda não foi aprovado.")

    payload = action.payload or {}
    path = payload.get("path")
    if not path or not await asyncio.to_thread(os.path.isfile, path):
        raise HTTPException(status_code=404, detail="Arquivo do artefato indisponível.")

    media_type = _ARTIFACT_MEDIA_TYPES.get(payload.get("fmt"), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=os.path.basename(path))


@router.get("/status", response_model=SourceStatusResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def source_status(
    request: Request,
    session: Session = Depends(get_current_session),
) -> SourceStatusResponse:
    """Report which sources are connected for this session, plus the folder's ingestion summary."""
    res = await registry.get(session.id)
    doc_count, page_count = await IngestedFileRepository().get_summary(session.user_id, session.agent_id)
    indexing = is_ingesting(session.user_id, session.agent_id)
    if res is None:
        return SourceStatusResponse(db_connected=False, doc_count=doc_count, page_count=page_count, indexing=indexing)
    return SourceStatusResponse(
        db_connected=res.db is not None,
        dialect=res.db_dialect,
        folder=res.folder,
        doc_count=doc_count,
        page_count=page_count,
        indexing=indexing,
    )


@router.post("/disconnect", response_model=DisconnectResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_connect"][0])
async def disconnect_sources(
    request: Request,
    session: Session = Depends(get_current_session),
) -> DisconnectResponse:
    """Tear down all sources (dispose the DB engine, release the granted folder)."""
    await registry.disconnect(session.id)
    return DisconnectResponse(message="Fontes desconectadas.")
