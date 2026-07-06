"""Session module.

Note: the episodic-event classes (``SessionEvent``, ``SessionEventRepository``) are intentionally
NOT re-exported here. This package ``__init__`` is imported very early in the app's import chain
(via ``session_dto``), before the ``agent`` table is registered in SQLModel's metadata. Pulling
``event_model`` in here would register ``sessionevent`` (which has a FK to ``agent``) and then
trigger the import-time ``create_all`` while ``agent`` is still absent — a ``NoReferencedTableError``.
Import them straight from their submodules instead (see ``event_repository`` / ``event_model``).
"""

from src.app.core.session.session_repository import SessionRepository

__all__ = ["Session", "SessionRepository"]
