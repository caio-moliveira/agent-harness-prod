"""Deep-agent middleware that caps oversized tool results before they reach the model.

A single very large tool result — a big ``read_file``, or a ``consultar_dados`` returning thousands
of rows — can blow the context window in one turn, *before* the bundled summarizer (which acts on
gradual, whole-history growth) would kick in. This trims such results to a head/tail preview **in the
model request only**: the full result stays in the graph state (nothing is lost, and nothing is
offloaded to a filesystem path the sandboxed ``read_file`` couldn't reach), the model simply never
sees the oversized body. It runs on every model call — streaming and non-streaming — inside the
deep-agent graph, and is idempotent (an already-small message passes through untouched).

This complements, rather than replaces, ``core/context/context_manager`` (which evicts to disk) —
that approach doesn't fit this agent's sandbox, so we trim in place here instead.
"""

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage

from src.app.core.common.logging import logger

# ~20k tokens. Above this, a single tool result dominates the window and is trimmed to a preview.
MAX_TOOL_RESULT_CHARS = 80_000
_PREVIEW_HEAD_CHARS = 4_000
_PREVIEW_TAIL_CHARS = 2_000


def _preview(content: str) -> str:
    """A head/tail preview of an oversized tool result, with a note on how to get more."""
    head = content[:_PREVIEW_HEAD_CHARS]
    tail = content[-_PREVIEW_TAIL_CHARS:]
    return (
        f"[Resultado muito grande ({len(content):,} caracteres) — truncado para caber no contexto. "
        "Para ver mais, refine a leitura/consulta (ex.: filtre linhas, leia um trecho específico, "
        "agregue no banco) em vez de trazer tudo.]\n\n"
        f"--- início ---\n{head}\n…\n--- fim ---\n{tail}"
    )


class ToolResultCapMiddleware(AgentMiddleware):
    """Trim oversized tool results to a preview in the model request; graph state is untouched."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        """Replace any over-threshold ToolMessage with a preview before the model call."""
        capped = 0
        trimmed: list = []
        for message in request.messages:
            if (
                isinstance(message, ToolMessage)
                and isinstance(message.content, str)
                and len(message.content) > MAX_TOOL_RESULT_CHARS
            ):
                trimmed.append(
                    ToolMessage(
                        content=_preview(message.content),
                        name=message.name,
                        tool_call_id=message.tool_call_id,
                    )
                )
                capped += 1
            else:
                trimmed.append(message)

        if capped:
            logger.info("tool_results_capped", count=capped)
            request = request.override(messages=trimmed)
        return await handler(request)
