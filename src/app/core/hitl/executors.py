"""Default executors for confirmation-gated actions (#19).

Executors run the actual side-effect ONLY after the user confirms. The bundled ``export_artifact``
is a safe placeholder (records the export) — real outward integrations (email, publish) register
their own executor for their action type as they are added.
"""

from src.app.core.common.logging import logger
from src.app.core.hitl.pending_model import PendingAction
from src.app.core.hitl.service import register_executor


async def _export_artifact(action: PendingAction) -> dict:
    """Placeholder export: the confirmed action's payload would be sent out here."""
    logger.info("artifact_export_executed", action_id=action.id, path=action.payload.get("path"))
    return {"exported": True, "path": action.payload.get("path")}


def register_default_executors() -> None:
    """Register the built-in executors. Idempotent."""
    register_executor("export_artifact", _export_artifact)
