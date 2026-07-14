"""Unit tests for DeliverableCompletionMiddleware — the mid-plan completion nudge.

Exercises the decision logic against synthetic agent states: nudge only when the model ends the turn
(AIMessage, no tool calls) with an incomplete plan and no deliverable tool called yet, bounded by the
nudge budget.
"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from deepagents import create_deep_agent

from src.app.agents.data_agent.completion_middleware import DeliverableCompletionMiddleware


def _ai(content="", tool_calls=None):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def _tool_call(name):
    return {"name": name, "args": {}, "id": f"call_{name}"}


def _mw():
    return DeliverableCompletionMiddleware()


def test_nudges_when_ending_mid_plan():
    """Ends with a plain AIMessage while a todo is in_progress and no deliverable ran → jump to model."""
    state = {
        "messages": [HumanMessage(content="faça o relatório"), _ai(content="Concluí a análise.")],
        "todos": [{"content": "Gerar relatório em Word", "status": "in_progress"}],
    }
    out = _mw().after_model(state, runtime=None)
    assert out is not None
    assert out["jump_to"] == "model"
    assert out["deliverable_nudges"] == 1
    assert isinstance(out["messages"][0], HumanMessage)


def test_no_nudge_when_plan_complete():
    """All todos completed → the agent legitimately finished."""
    state = {
        "messages": [_ai(content="Pronto.")],
        "todos": [{"content": "Gerar relatório", "status": "completed"}],
    }
    assert _mw().after_model(state, runtime=None) is None


def test_no_nudge_when_last_message_has_tool_calls():
    """The model is still working (about to run a tool) — not ending."""
    state = {
        "messages": [_ai(tool_calls=[_tool_call("consultar_dados")])],
        "todos": [{"content": "x", "status": "in_progress"}],
    }
    assert _mw().after_model(state, runtime=None) is None


def test_no_nudge_when_deliverable_already_called():
    """gerar_artefato was already invoked this run (e.g. parked for approval) → never double-nudge."""
    state = {
        "messages": [
            _ai(tool_calls=[_tool_call("gerar_artefato")]),
            ToolMessage(content="artefato aguardando aprovação", tool_call_id="call_gerar_artefato"),
            _ai(content="Deixei o arquivo aguardando sua aprovação."),
        ],
        "todos": [{"content": "Gerar relatório", "status": "in_progress"}],
    }
    assert _mw().after_model(state, runtime=None) is None


def test_no_nudge_when_no_todos():
    """A plain conversational turn with no plan is never nudged."""
    state = {"messages": [_ai(content="Olá!")], "todos": []}
    assert _mw().after_model(state, runtime=None) is None


def test_nudge_budget_is_bounded():
    """Once the nudge budget is spent, stop (the call-limit middleware is the ultimate backstop)."""
    state = {
        "messages": [_ai(content="ainda não gerei")],
        "todos": [{"content": "Gerar relatório", "status": "pending"}],
        "deliverable_nudges": 2,
    }
    assert _mw().after_model(state, runtime=None) is None


# --- Runtime regression guard -------------------------------------------------------------------
# The dict-based tests above pass `todos` in directly, so they would NOT catch the real bug: the
# framework filters each middleware's state to its `state_schema`, so if the schema omits `todos`,
# `state["todos"]` is None at runtime and the nudge never fires. This test builds a real deep agent
# with a fake model that stops mid-plan and asserts the deliverable tool actually gets called.


@tool
def gerar_artefato(titulo: str) -> str:
    """Gera o artefato (fake)."""
    return "artefato gerado"


def _has_toolmsg(messages, name: str) -> bool:
    return any(isinstance(m, ToolMessage) and m.name == name for m in messages)


class _StopMidPlanModel(BaseChatModel):
    """Plans, then stops with plain text (no deliverable). Complies only once nudged."""

    @property
    def _llm_type(self) -> str:
        return "fake-stop-mid-plan"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001
        saw_nudge = any(
            isinstance(m, HumanMessage) and "NÃO concluiu" in (m.content or "") for m in messages
        )
        if _has_toolmsg(messages, "gerar_artefato"):
            msg = AIMessage(content="Arquivo gerado.")
        elif saw_nudge:
            msg = AIMessage(content="", tool_calls=[{"name": "gerar_artefato", "args": {"titulo": "Q4"}, "id": "c2"}])
        elif not _has_toolmsg(messages, "write_todos"):
            msg = AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_todos",
                    "args": {"todos": [{"content": "Gerar relatório", "status": "in_progress"}]},
                    "id": "c1",
                }],
            )
        else:
            msg = AIMessage(content="Concluí a análise.")  # premature stop
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.mark.asyncio
async def test_nudge_fires_at_runtime_and_calls_deliverable():
    """End-to-end: a model that stops mid-plan is nudged and ends up calling the deliverable tool."""
    agent = create_deep_agent(
        model=_StopMidPlanModel(),
        tools=[gerar_artefato],
        system_prompt="teste",
        middleware=[DeliverableCompletionMiddleware()],
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="gere o relatório")]})
    called = [tc["name"] for m in result["messages"] for tc in (getattr(m, "tool_calls", None) or [])]
    assert "gerar_artefato" in called, f"deliverable tool never called; tool calls were {called}"
