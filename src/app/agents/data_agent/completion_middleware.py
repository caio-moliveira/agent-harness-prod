"""Deep-agent middleware that keeps the agent from ending mid-plan without producing the deliverable.

Some models (notably non-Claude ones) gather all the data, mark the plan's final "generate the file"
step as in_progress, and then end the turn with a short text conclusion — never calling the
deliverable tool (``gerar_artefato`` / ``gerar_planilha`` / ``write_file``). This middleware detects
that exact situation — the model produced a final message with **no tool calls** while the plan still
has unfinished todos **and** no deliverable tool has been called this run — and jumps back to the
model, up to ``max_nudges`` times, with a firm instruction to finish.

It is **model-agnostic**: it fixes premature-stop behavior for weaker instruction followers without
changing the provider. A verified repro shows a focused nudge makes the model emit the tool call it
skipped. The ``ModelCallLimitMiddleware`` cap is the ultimate backstop against any loop.
"""

from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.todo import PlanningState
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage
from typing_extensions import NotRequired

from src.app.core.common.logging import logger

# Tools that actually produce the deliverable. Once any of these has been called this run, the model
# has done its job (it may be parked awaiting approval) — we must never nudge into a double-generation.
_DELIVERABLE_TOOLS = {"gerar_artefato", "gerar_planilha", "write_file"}
_MAX_NUDGES = 2

_NUDGE = (
    "Você ainda NÃO concluiu esta tarefa. O plano tem passo(s) pendente(s): {pending}. "
    "CHAME AGORA a ferramenta de geração: gerar_artefato (relatório/documento/apresentação em "
    "docx/pptx) ou gerar_planilha (Excel/planilha). Você NÃO precisa de autorização para chamá-la — "
    "chame diretamente; a aprovação do usuário acontece automaticamente DEPOIS. NÃO redija o "
    "conteúdo em Markdown/texto no chat, NÃO espere permissão e NÃO encerre o turno enquanto a "
    "ferramenta de geração não tiver rodado."
)


class DeliverableCompletionState(PlanningState):
    """Extends the planning state (so the middleware can READ ``todos``) with a bounded nudge counter.

    Inheriting from ``PlanningState`` is essential: the framework filters the state each middleware
    sees to its own ``state_schema``, so a bare ``AgentState`` would make ``state["todos"]`` always
    ``None`` and the nudge would never fire.
    """

    deliverable_nudges: NotRequired[int]


def _deliverable_called(messages: list) -> bool:
    """True if a deliverable tool was already invoked this run (so we must not nudge again)."""
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") in _DELIVERABLE_TOOLS:
                return True
    return False


class DeliverableCompletionMiddleware(AgentMiddleware):
    """Nudge the model back to work when it tries to end the turn with an incomplete plan."""

    state_schema = DeliverableCompletionState

    def __init__(self, max_nudges: int = _MAX_NUDGES) -> None:
        """Store the per-run nudge budget (``max_nudges`` jumps back to the model before giving up)."""
        super().__init__()
        self.max_nudges = max_nudges

    def _maybe_nudge(self, state: Any) -> Optional[dict[str, Any]]:
        """Return a jump-to-model command when the agent is ending mid-plan, else None."""
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        # Only act when the model just tried to END the turn: an AIMessage with no tool calls.
        if not isinstance(last, AIMessage) or (getattr(last, "tool_calls", None) or []):
            return None
        todos = state.get("todos") or []
        unfinished = [t for t in todos if isinstance(t, dict) and t.get("status") != "completed"]
        if not unfinished:
            return None
        # The model already produced (or parked) the deliverable — don't nudge into a duplicate.
        if _deliverable_called(messages):
            return None
        nudges = state.get("deliverable_nudges", 0)
        if nudges >= self.max_nudges:
            logger.info("deliverable_nudge_budget_exhausted", nudges=nudges)
            return None
        pending = "; ".join(t.get("content", "") for t in unfinished)
        logger.info("deliverable_completion_nudge", nudge_index=nudges + 1, pending=pending[:200])
        return {
            "jump_to": "model",
            "messages": [HumanMessage(content=_NUDGE.format(pending=pending))],
            "deliverable_nudges": nudges + 1,
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> Optional[dict[str, Any]]:
        """Sync path — jump back to the model with a nudge when ending mid-plan."""
        return self._maybe_nudge(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> Optional[dict[str, Any]]:
        """Async path (used by the streaming deep agent) — same logic as :meth:`after_model`."""
        return self._maybe_nudge(state)
