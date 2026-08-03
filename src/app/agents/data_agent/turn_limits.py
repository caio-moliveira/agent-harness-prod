"""Turn-limit policy for the deep agent: derived recursion limit + subagent call caps.

Three independent layers bound a turn (see AGENTS.md "Turn limits"):

1. **Semantic cap** — ``ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT, exit_behavior="end")``:
   the only limit a legitimate turn should ever hit; the agent ends gracefully and the client gets
   a "continue" affordance.
2. **Physical backstop** — LangGraph's ``recursion_limit``, which must NEVER fire before (1). In
   LangChain v1 every middleware ``before_model``/``after_model`` hook is its own graph node, and
   every node executed consumes one super-step — so a model→tools round costs ~8-10 steps with this
   agent's stack, not 2. The limit is therefore DERIVED from the compiled graph
   (:func:`compute_recursion_limit`), never guessed with a constant.
3. **Temporal backstop** — ``TURN_TIMEOUT_SECONDS``, enforced at the API layer around the stream.

Subagents (the deepagents ``task`` tool) inherit the parent's ``recursion_limit`` *value* and run
with NO model-call cap of their own by default (verified empirically against deepagents 0.4.3,
cf. deepagents#1698) — so a runaway subagent burns the whole recursion budget and kills the parent
turn with ``GraphRecursionError``. :func:`cap_subagent_specs` gives every declarative subagent its
own graceful ``ModelCallLimitMiddleware`` so the runaway ends politely inside the subagent and
returns as a normal tool result instead.
"""

from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.runnables import Runnable

from src.app.core.common.config import settings

# Rounds of slack past the call cap (the graceful end itself consumes extra steps), plus a flat
# slack for the run-once nodes (*.before_agent / *.after_agent) and jump_to re-entries.
_EXTRA_ROUNDS = 2
_SLACK_STEPS = 20

# Fallback steps-per-round when the graph can't be introspected (never expected in practice;
# matches the measured cost of the current stack so the limit stays safe rather than tight).
_FALLBACK_STEPS_PER_ROUND = 10


def loop_steps_per_round(agent: Runnable) -> int:
    """Super-steps one model→tools round costs in ``agent``'s compiled graph.

    Counts the per-round nodes: every middleware ``*.before_model``/``*.after_model`` hook node
    plus the ``model`` and ``tools`` nodes themselves. Run-once nodes (``*.before_agent`` /
    ``*.after_agent``) are excluded — they are covered by the flat slack instead.
    """
    try:
        nodes = agent.get_graph().nodes
    except Exception:  # pragma: no cover - introspection is best-effort by design
        return _FALLBACK_STEPS_PER_ROUND
    hook_nodes = sum(1 for name in nodes if str(name).endswith((".before_model", ".after_model")))
    return hook_nodes + 2  # + the model node and the tools node


def compute_recursion_limit(agent: Runnable, call_limit: int | None = None) -> int:
    """Derive the recursion limit that guarantees the graceful call cap fires first.

    ``steps_per_round × (MODEL_CALL_LIMIT + slack rounds) + flat slack`` — recomputed from the
    actual compiled graph, so adding/removing middleware never silently re-introduces the
    "GraphRecursionError before the graceful cap" bug (measured in the 2026-07-31 E2E).
    """
    limit = call_limit if call_limit is not None else settings.MODEL_CALL_LIMIT
    return loop_steps_per_round(agent) * (limit + _EXTRA_ROUNDS) + _SLACK_STEPS


def cap_subagent_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every declarative subagent spec its own graceful model-call cap (in place).

    deepagents extends a declarative spec's ``middleware`` list into the compiled subagent, so the
    cap rides along. Runnable specs (``CompiledSubAgent``, e.g. deep research) are left as-is:
    their graphs are bounded by their own design and inherit the parent's generous derived
    recursion limit.
    """
    for spec in specs:
        if "runnable" in spec:
            continue
        middleware = list(spec.get("middleware", []))
        middleware.append(
            ModelCallLimitMiddleware(run_limit=settings.MODEL_CALL_LIMIT, exit_behavior="end")
        )
        spec["middleware"] = middleware
    return specs
