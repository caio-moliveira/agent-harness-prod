"""Artifact generation: turn structured, sourced content into Word/PowerPoint deliverables.

``generate_artifact`` is the entry point — it dispatches by format, renders off the event loop, and
(when given session context) records an ``artifact_generated`` event in the episodic log (#10).
Traceability is enforced at render time via ``spec`` (every claim shows its source or an explicit
unsourced marker).
"""

import asyncio
from typing import Optional

from src.app.core.artifacts.docx_renderer import render_docx
from src.app.core.artifacts.pptx_renderer import render_pptx
from src.app.core.artifacts.spec import (
    ArtifactSpec,
    Claim,
    Section,
    Sheet,
    SpreadsheetSpec,
    Template,
    claim_suffix,
    unsourced_claims,
)
from src.app.core.artifacts.xlsx_renderer import render_xlsx
from src.app.core.common.logging import logger
from src.app.core.session.event_model import SessionEventType
from src.app.core.session.event_repository import SessionEventRepository

_RENDERERS = {"docx": render_docx, "pptx": render_pptx}

_event_repo = SessionEventRepository()


async def generate_artifact(
    spec: ArtifactSpec,
    fmt: str,
    path: str,
    template: Optional[Template] = None,
    user_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    session_id: Optional[str] = None,
) -> str:
    """Render ``spec`` to ``fmt`` (``docx``|``pptx``) at ``path``; audit it when session-scoped."""
    renderer = _RENDERERS.get(fmt)
    if renderer is None:
        raise ValueError(f"Formato de artefato não suportado: {fmt}")

    # Rendering is CPU/IO-bound (zip + XML) — keep it off the event loop.
    await asyncio.to_thread(renderer, spec, path, template)

    if user_id is not None and session_id is not None:
        try:
            await _event_repo.record_event(
                user_id=user_id,
                session_id=session_id,
                event_type=SessionEventType.ARTIFACT_GENERATED,
                agent_id=agent_id,
                payload={"format": fmt, "title": spec.title, "unsourced": len(unsourced_claims(spec))},
                scope="artifact",
            )
        except Exception:  # noqa: BLE001 - auditing must never fail the deliverable
            logger.exception("artifact_event_record_failed", session_id=session_id, fmt=fmt)

    logger.info("artifact_generated", fmt=fmt, title=spec.title, unsourced=len(unsourced_claims(spec)))
    return path


async def generate_spreadsheet(
    spec: SpreadsheetSpec,
    path: str,
    user_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    session_id: Optional[str] = None,
) -> str:
    """Render ``spec`` to a native .xlsx workbook at ``path``; audit it when session-scoped."""
    # Rendering is CPU/IO-bound (zip + XML) — keep it off the event loop.
    await asyncio.to_thread(render_xlsx, spec, path, None)

    if user_id is not None and session_id is not None:
        try:
            await _event_repo.record_event(
                user_id=user_id,
                session_id=session_id,
                event_type=SessionEventType.ARTIFACT_GENERATED,
                agent_id=agent_id,
                payload={"format": "xlsx", "title": spec.title, "sheets": len(spec.sheets)},
                scope="artifact",
            )
        except Exception:  # noqa: BLE001 - auditing must never fail the deliverable
            logger.exception("artifact_event_record_failed", session_id=session_id, fmt="xlsx")

    logger.info("artifact_generated", fmt="xlsx", title=spec.title, sheets=len(spec.sheets))
    return path


__all__ = [
    "ArtifactSpec",
    "Claim",
    "Section",
    "Sheet",
    "SpreadsheetSpec",
    "Template",
    "claim_suffix",
    "unsourced_claims",
    "render_docx",
    "render_pptx",
    "render_xlsx",
    "generate_artifact",
    "generate_spreadsheet",
]
