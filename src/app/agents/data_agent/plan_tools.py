"""Plan-approval tool for the Data Agent (built on the #19 HITL gate).

Lets the agent propose a plan and pause for the user's approval **before** doing large, multi-step,
or irreversible work. Like the artifact tools, it does not act inline: it parks an ``approve_plan``
request for confirmation. The plan itself is not a side-effect, so its executor is a no-op — approval
simply unblocks the agent, which resumes and executes on the next turn (the UI auto-sends a
"proceed" message on approval). Bound to one ``(user_id, agent_id, session_id)`` so the parked action
is attributed and isolated to the session that produced it.
"""

from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.app.core.common.logging import logger
from src.app.init import hitl_service


def make_plan_tools(
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: Optional[str],
) -> list[BaseTool]:
    """Build the plan-approval tool bound to one session. Empty list without a user/session context."""
    if user_id is None or not session_id:
        return []

    @tool
    async def propor_plano(titulo: str, passos: list[str]) -> str:
        """Propõe um plano e PAUSA para aprovação do usuário antes de executá-lo.

        Use ANTES de tarefas grandes, com muitos passos, ou irreversíveis (ex.: gerar vários
        artefatos, uma análise longa em várias etapas), quando vale a pena o usuário revisar o plano
        primeiro. Não use para perguntas simples ou de um passo só. Envie um ``titulo`` curto e
        ``passos`` como uma lista de etapas objetivas. Após enviar, PARE e aguarde a aprovação —
        você prosseguirá quando o usuário aprovar.
        """
        steps = [str(p).strip() for p in (passos or []) if str(p).strip()]
        if not steps:
            return "Nada a propor: envie ao menos um passo no plano."

        try:
            action = await hitl_service.request(
                user_id,
                session_id,
                "approve_plan",
                {"title": titulo, "steps": steps, "agent_id": agent_id},
            )
        except Exception:
            logger.exception("plan_request_failed", session_id=session_id)
            return "Falha ao registrar o plano. Tente novamente."

        return (
            f"Propus o plano '{titulo}' com {len(steps)} passo(s), aguardando sua aprovação "
            f"(id {action.id}). Aprove para eu executar, ou recuse para eu replanejar. "
            "Vou aguardar sua decisão."
        )

    return [propor_plano]
