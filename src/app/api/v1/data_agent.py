"""Data Agent API: connect a read-only database, grant a folder (phase 2), and query.

Credentials are received over the authenticated session, used to build an in-memory
connection held by the per-session registry, and never persisted or logged.
"""

import asyncio
import json
import os
from urllib.parse import quote_plus

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse
from langchain_community.utilities import SQLDatabase

from src.app.agents.data_agent import build_data_agent
from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_session
from src.app.api.v1.dtos.data_agent import (
    ConnectDbRequest,
    ConnectDbResponse,
    DataQueryRequest,
    DataQueryResponse,
    DataStreamRequest,
    DisconnectResponse,
    GrantFolderRequest,
    GrantFolderResponse,
    SourceStatusResponse,
)
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.common.model.message import Message
from src.app.core.sandbox import registry
from src.app.core.sandbox.docker_sandbox import DockerSandbox, create_container
from src.app.core.sandbox.paths import is_within_allowed_roots, validate_grantable_folder
from src.app.core.sandbox.registry import SessionResources
from src.app.core.session.session_model import Session
from src.app.init import agent_repository

router = APIRouter()


def _build_db_url(body: ConnectDbRequest) -> str:
    """Build a SQLAlchemy URL from connection credentials (URL-encoded)."""
    user = quote_plus(body.username)
    pwd = quote_plus(body.password.get_secret_value())
    url = f"{body.driver}://{user}:{pwd}@{body.host}:{body.port}/{body.database}"
    if body.sslmode:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode={body.sslmode}"
    return url


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

    url = _build_db_url(body)
    try:
        db = await asyncio.to_thread(SQLDatabase.from_uri, url, None, sample_rows_in_table_info=3)
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
    session: Session = Depends(get_current_session),
) -> GrantFolderResponse:
    """Grant read-only access to a host folder by mounting it into an isolated sandbox."""
    # Security: folder must exist and resolve under a configured allow-listed root.
    path = validate_grantable_folder(body.path)

    logger.info("folder_grant_requested", session_id=session.id, folder=path)
    try:
        container_id = await create_container(path)
        backend = DockerSandbox(container_id)
    except Exception as e:
        logger.warning("sandbox_create_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail="Falha ao criar o sandbox. O Docker está em execução?")

    await registry.set_folder(session.id, path, container_id, backend)
    logger.info("folder_granted", session_id=session.id, folder=path, container_id=container_id)
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


async def _ensure_agent_folder(res: SessionResources, session: Session, folder: str) -> None:
    """Materialize the agent's bound folder into this session's sandbox, if not already up.

    Re-validates the folder against the allow-list on every use, so tightening
    ``SANDBOX_ALLOWED_ROOTS`` immediately revokes a stale binding. Degrades gracefully:
    a disabled sandbox or a Docker failure just leaves the agent without file tools rather
    than failing the whole chat.
    """
    if res.sandbox_backend is not None:
        return  # already materialized for this session
    if not settings.SANDBOX_ENABLED or not settings.SANDBOX_ALLOWED_ROOTS:
        return
    abspath = os.path.abspath(folder)
    if not os.path.isdir(abspath) or not is_within_allowed_roots(abspath, settings.SANDBOX_ALLOWED_ROOTS):
        logger.warning("agent_folder_binding_invalid", session_id=session.id, folder=abspath)
        return
    try:
        container_id = await create_container(abspath)
        backend = DockerSandbox(container_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_folder_sandbox_failed", session_id=session.id, error=str(e))
        return
    await registry.set_folder(session.id, abspath, container_id, backend)
    logger.info("agent_folder_materialized", session_id=session.id, folder=abspath)


async def _build_agent_for_session(res: SessionResources, session: Session):
    """Build a Data Agent from the session's live sources and its bound agent config.

    When the session is bound to an agent, the agent's system prompt is applied, its bound
    folder is materialized into the sandbox, and its id scopes long-term memory (per-agent
    isolation). Works with zero sources.
    """
    system_prompt = None
    name = "Data Agent"
    if session.agent_id is not None:
        agent = await agent_repository.get_agent(session.agent_id)
        if agent is not None:
            system_prompt = agent.system_prompt or None
            name = agent.name or name
            folder = (agent.config or {}).get("folder")
            if folder:
                await _ensure_agent_folder(res, session, folder)
    return build_data_agent(
        res,
        user_id=session.user_id,
        system_prompt=system_prompt,
        agent_id=session.agent_id,
        name=name,
    )


async def _get_or_build_agent(session: Session):
    """Return the session's Data Agent, building it if needed (works with zero sources)."""
    res = await registry.ensure(session.id)
    if res.agent is None:
        res.agent = await _build_agent_for_session(res, session)
    return res.agent


@router.post("/query/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def query_stream(
    request: Request,
    body: DataStreamRequest,
    session: Session = Depends(get_current_session),
) -> StreamingResponse:
    """Stream the Data Agent's work (tool calls, reasoning, tokens) as SSE events."""
    agent = await _get_or_build_agent(session)
    logger.info("data_stream_started", session_id=session.id, message_count=len(body.messages))

    async def event_generator():
        try:
            async for ev in agent.astream_query_events(body.messages, session.id, session.user_id):
                yield f"data: {json.dumps(ev)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception:
            logger.exception("data_stream_failed", session_id=session.id)
            yield f"data: {json.dumps({'type': 'error', 'content': 'Erro ao processar a consulta.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/status", response_model=SourceStatusResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_agent"][0])
async def source_status(
    request: Request,
    session: Session = Depends(get_current_session),
) -> SourceStatusResponse:
    """Report which sources are connected for this session."""
    res = await registry.get(session.id)
    if res is None:
        return SourceStatusResponse(db_connected=False)
    return SourceStatusResponse(
        db_connected=res.db is not None,
        dialect=res.db_dialect,
        folder=res.folder,
    )


@router.post("/disconnect", response_model=DisconnectResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["data_connect"][0])
async def disconnect_sources(
    request: Request,
    session: Session = Depends(get_current_session),
) -> DisconnectResponse:
    """Tear down all sources (dispose the DB engine, remove the sandbox container)."""
    await registry.disconnect(session.id)
    return DisconnectResponse(message="Fontes desconectadas.")
