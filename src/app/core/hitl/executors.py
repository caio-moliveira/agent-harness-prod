"""Default executors for confirmation-gated actions (#19).

Executors run the actual side-effect ONLY after the user confirms. ``export_artifact`` renders the
requested Word/PowerPoint deliverable (and records the ``artifact_generated`` event that feeds
metrics/reflection) — so an artifact only ever hits disk once its owner approves it. Real outward
integrations (email, publish) register their own executor for their action type as they are added.
"""

import os

from src.app.core.artifacts import ArtifactSpec, SpreadsheetSpec, generate_artifact, generate_spreadsheet
from src.app.core.common.logging import logger
from src.app.core.hitl.pending_model import PendingAction
from src.app.core.hitl.service import register_executor
from src.app.core.learning import bg_run_reflection
from src.app.core.memory.agent_memory_service import AgentMemoryKind, bg_record_memory


async def _export_artifact(action: PendingAction) -> dict:
    """Render the confirmed artifact from its parked spec; no-op for a spec-less payload."""
    payload = action.payload or {}
    spec_data, fmt, path = payload.get("spec"), payload.get("fmt"), payload.get("path")
    if not spec_data or not fmt or not path:
        # Legacy/empty payload — nothing to render, but confirmation still succeeds.
        logger.info("artifact_export_noop", action_id=action.id, path=path)
        return {"exported": True, "path": path}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if payload.get("kind") == "spreadsheet":
        await generate_spreadsheet(
            SpreadsheetSpec(**spec_data),
            path,
            user_id=action.user_id,
            agent_id=payload.get("agent_id"),
            session_id=action.session_id,
        )
    else:
        await generate_artifact(
            ArtifactSpec(**spec_data),
            fmt,
            path,
            user_id=action.user_id,
            agent_id=payload.get("agent_id"),
            session_id=action.session_id,
        )
    logger.info("artifact_export_executed", action_id=action.id, path=path, fmt=fmt)
    # A new artifact_generated event just landed — refresh the agent's learned preferences (#20).
    bg_run_reflection(action.user_id, payload.get("agent_id"))
    # Record the deliverable as an outcome memory (#23) so a future session knows it was already
    # produced (and where), instead of regenerating it. Summary is embedded; path rides in refs.
    title = (spec_data or {}).get("title") or "documento"
    is_sheet = payload.get("kind") == "spreadsheet"
    noun = "a planilha" if is_sheet else "o relatório"
    bg_record_memory(
        action.user_id,
        payload.get("agent_id"),
        action.session_id,
        AgentMemoryKind.OUTCOME,
        summary=f"Gerei {noun} '{title}' ({fmt}) — já entregue, não refazer sem novo pedido.",
        body={"title": title, "format": fmt, "kind": "spreadsheet" if is_sheet else "document"},
        refs={"path": path},
    )
    return {"exported": True, "path": path, "format": fmt}


async def _approve_plan(action: PendingAction) -> dict:
    """Confirm a proposed plan.

    There is no side-effect to run — approval simply unblocks the agent, which resumes and executes
    on the next turn — so this executor just records the decision.
    """
    payload = action.payload or {}
    logger.info("plan_approved", action_id=action.id, steps=len(payload.get("steps") or []))
    return {"approved": True, "title": payload.get("title")}


def register_default_executors() -> None:
    """Register the built-in executors. Idempotent."""
    register_executor("export_artifact", _export_artifact)
    register_executor("approve_plan", _approve_plan)
