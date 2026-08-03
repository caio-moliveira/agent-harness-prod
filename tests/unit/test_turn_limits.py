"""Unit tests for the turn-limit policy (turn_limits.py) — the graceful cap must always win.

Regression suite for the 2026-07-31 E2E finding: in LangChain v1 every middleware
``before_model``/``after_model`` hook is a graph node consuming one recursion super-step per
round (~8-10 with the data agent's stack), so a guessed ``2 × MODEL_CALL_LIMIT`` recursion limit
made ``GraphRecursionError`` fire long before ``ModelCallLimitMiddleware`` could end the turn
gracefully. No network: a scripted fake chat model drives the real deepagents graph.
"""

from typing import Any, Optional

import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, PIIMiddleware
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.app.agents.data_agent.turn_limits import (
    cap_subagent_specs,
    compute_recursion_limit,
    loop_steps_per_round,
)


class FakeToolCallingModel(BaseChatModel):
    """Scripted chat model: emits each scripted response in order; repeats the last forever.

    A step is ``{"content": str}`` (final text) or ``{"tool": name, "args": {...}}`` (tool call).
    ``calls`` counts model invocations — the graceful cap must stop the loop at the run limit.
    """

    script: list[dict]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """Accept any tool binding — the script decides what gets 'called'."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        step = self.script[min(self.calls, len(self.script) - 1)]
        object.__setattr__(self, "calls", self.calls + 1)
        if "tool" in step:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": step["tool"], "args": step["args"], "id": f"c{self.calls}"}],
            )
        else:
            msg = AIMessage(content=step["content"])
        return ChatResult(generations=[ChatGeneration(message=msg)])


_RUNAWAY_LS = [{"tool": "ls", "args": {"path": "/"}}]  # repeats forever → runaway tool loop


def _build_agent(model: BaseChatModel, call_limit: int):
    """A deep agent with the same *kind* of custom stack the data agent uses (PII + call cap)."""
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt="test",
        middleware=[
            PIIMiddleware("email"),
            ModelCallLimitMiddleware(run_limit=call_limit, exit_behavior="end"),
        ],
    )


def test_loop_steps_reflect_middleware_hooks():
    """Each before/after_model hook is a graph node: the per-round cost is far above the old 2."""
    agent = _build_agent(FakeToolCallingModel(script=_RUNAWAY_LS), call_limit=5)
    steps = loop_steps_per_round(agent)
    node_names = list(agent.get_graph().nodes)
    hooks = [n for n in node_names if n.endswith((".before_model", ".after_model"))]
    assert steps == len(hooks) + 2  # + model + tools
    assert steps >= 5  # the old formula assumed 2 — the real stack is far costlier


def test_recursion_limit_scales_with_call_limit():
    """The derived limit grows with the cap and always dominates steps×rounds."""
    agent = _build_agent(FakeToolCallingModel(script=_RUNAWAY_LS), call_limit=5)
    steps = loop_steps_per_round(agent)
    assert compute_recursion_limit(agent, 10) >= steps * 10
    assert compute_recursion_limit(agent, 50) > compute_recursion_limit(agent, 10)


@pytest.mark.asyncio
async def test_runaway_loop_ends_gracefully_at_call_cap():
    """A tool loop must end via ModelCallLimitMiddleware at the cap — never GraphRecursionError."""
    call_limit = 5
    model = FakeToolCallingModel(script=_RUNAWAY_LS)
    agent = _build_agent(model, call_limit)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": compute_recursion_limit(agent, call_limit)},
    )
    assert model.calls == call_limit  # stopped exactly at the graceful cap
    # The middleware injects its "limit exceeded" note as the final message — the turn ended
    # politely with a message, not an exception.
    assert result["messages"], "graph must return the accumulated messages"


@pytest.mark.asyncio
async def test_old_formula_would_have_crashed():
    """Sanity: with the OLD 2×cap+20 limit the same runaway dies hard — the bug this suite guards."""
    from langgraph.errors import GraphRecursionError

    call_limit = 5
    model = FakeToolCallingModel(script=_RUNAWAY_LS)
    agent = _build_agent(model, call_limit)
    with pytest.raises(GraphRecursionError):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"recursion_limit": 2 * call_limit + 20},
        )


@pytest.mark.asyncio
async def test_runaway_subagent_ends_gracefully_with_cap(monkeypatch):
    """A capped declarative subagent ends its own runaway politely instead of killing the parent.

    Without the cap the subagent (which inherits the parent's recursion budget but has no
    model-call limit of its own — deepagents#1698 territory) loops until GraphRecursionError
    propagates through the ``task`` tool and kills the whole turn.
    """
    from src.app.agents.data_agent import turn_limits

    monkeypatch.setattr(turn_limits.settings, "MODEL_CALL_LIMIT", 4, raising=False)

    parent_model = FakeToolCallingModel(script=[
        {"tool": "task", "args": {"description": "loop", "subagent_type": "looper"}},
        {"content": "done"},
    ])
    sub_model = FakeToolCallingModel(script=_RUNAWAY_LS)
    specs = cap_subagent_specs([
        {"name": "looper", "description": "loops", "system_prompt": "sub", "model": sub_model}
    ])
    agent = create_deep_agent(
        model=parent_model,
        tools=[],
        system_prompt="parent",
        subagents=specs,
        middleware=[ModelCallLimitMiddleware(run_limit=6, exit_behavior="end")],
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": compute_recursion_limit(agent, 6)},
    )
    assert sub_model.calls == 4  # subagent stopped at ITS graceful cap
    assert parent_model.calls == 2  # parent resumed and answered after the delegation returned
    assert result["messages"]


def test_cap_subagent_specs_leaves_runnable_specs_alone():
    """CompiledSubAgent specs (e.g. deep research) pass through untouched."""
    sentinel = object()
    specs = cap_subagent_specs([
        {"name": "dr", "description": "x", "runnable": sentinel},
        {"name": "sql", "description": "y", "system_prompt": "z"},
    ])
    assert "middleware" not in specs[0]
    assert any(isinstance(m, ModelCallLimitMiddleware) for m in specs[1]["middleware"])
