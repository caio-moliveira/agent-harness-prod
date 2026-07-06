"""Human-in-the-loop confirmation for outward-facing actions (#19).

Public surface: ``HitlService`` (request/confirm/reject), ``register_executor``, the
``PendingActionRepository`` and the ``PendingAction`` model.
"""

from src.app.core.hitl.pending_model import PendingAction, PendingActionStatus
from src.app.core.hitl.repository import PendingActionRepository
from src.app.core.hitl.service import ConfirmationError, HitlService, register_executor

__all__ = [
    "PendingAction",
    "PendingActionStatus",
    "PendingActionRepository",
    "HitlService",
    "ConfirmationError",
    "register_executor",
]
