"""Imports every SQLModel table so ``SQLModel.metadata`` is fully populated.

Alembic's ``env.py`` imports this module before autogenerate so the metadata it diffs against the
database includes every table. Importing a model module is what registers its table on the shared
``SQLModel.metadata`` — a table absent here is invisible to autogenerate (it would propose dropping
it). Keep this list in sync when a new ``table=True`` model is added.
"""

# noqa: F401 — imported for their registration side effect, not for direct use.
from src.app.core.agent import agent_model  # noqa: F401
from src.app.core.hitl import pending_model  # noqa: F401
from src.app.core.ingestion import chunk_model, source_model  # noqa: F401
from src.app.core.learning import models as learning_models  # noqa: F401
from src.app.core.memory import agent_memory_model  # noqa: F401
from src.app.core.session import event_model, message_model, session_model  # noqa: F401
from src.app.core.skill import skill_model  # noqa: F401
from src.app.core.user import user_model  # noqa: F401
