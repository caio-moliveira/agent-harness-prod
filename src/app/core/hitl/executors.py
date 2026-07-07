"""Default executors for confirmation-gated actions (#19).

Executors run the actual side-effect ONLY after the user confirms. ``export_artifact`` renders the
requested Word/PowerPoint deliverable (and records the ``artifact_generated`` event that feeds
metrics/reflection) — so an artifact only ever hits disk once its owner approves it. Real outward
integrations (email, publish) register their own executor for their action type as they are added.
"""

import os

from src.app.core.artifacts import ArtifactSpec, generate_artifact
from src.app.core.common.logging import logger
from src.app.core.hitl.pending_model import PendingAction
from src.app.core.hitl.service import register_executor
from src.app.core.learning import bg_run_reflection


async def _export_artifact(action: PendingAction) -> dict:
    """Render the confirmed artifact from its parked spec; no-op for a spec-less payload."""
    payload = action.payload or {}
    spec_data, fmt, path = payload.get("spec"), payload.get("fmt"), payload.get("path")
    if not spec_data or not fmt or not path:
        # Legacy/empty payload — nothing to render, but confirmation still succeeds.
        logger.info("artifact_export_noop", action_id=action.id, path=path)
        return {"exported": True, "path": path}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    spec = ArtifactSpec(**spec_data)
    await generate_artifact(
        spec,
        fmt,
        path,
        user_id=action.user_id,
        agent_id=payload.get("agent_id"),
        session_id=action.session_id,
    )
    logger.info("artifact_export_executed", action_id=action.id, path=path, fmt=fmt)
    # A new artifact_generated event just landed — refresh the agent's learned preferences (#20).
    bg_run_reflection(action.user_id, payload.get("agent_id"))
    return {"exported": True, "path": path, "format": fmt}


def register_default_executors() -> None:
    """Register the built-in executors. Idempotent."""
    register_executor("export_artifact", _export_artifact)
