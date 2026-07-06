"""Continuous learning (#20): reflection, correction signals, and gated skill refinement.

Public surface: ``run_reflection`` / ``reflect_preferences`` (RF-18), ``propose_refinement``
(RF-19/20 — always draft, re-approval required), and the correction/preference repositories.

Deep memory consolidation (dedup/archival of mem0 entries — the remaining RF item) is deferred:
it needs the mem0 vector backend to exercise meaningfully and is tracked as a follow-up rather
than shipped untested here.
"""

from src.app.core.learning.models import AgentPreference, CorrectionSignal
from src.app.core.learning.refinement import propose_refinement
from src.app.core.learning.reflection import reflect_preferences, run_reflection
from src.app.core.learning.repository import CorrectionRepository, PreferenceRepository

__all__ = [
    "AgentPreference",
    "CorrectionSignal",
    "propose_refinement",
    "reflect_preferences",
    "run_reflection",
    "CorrectionRepository",
    "PreferenceRepository",
]
