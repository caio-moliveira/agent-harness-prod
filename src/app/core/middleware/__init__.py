"""Composable middleware for agent invocations.

Usage::

    from src.app.core.middleware import (
        AgentContext,
        AgentPipeline,
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        MemoryMiddleware,
        GuardrailMiddleware,
    )

    pipeline = AgentPipeline(
        middlewares=[LoggingMiddleware(), ErrorHandlingMiddleware(), MemoryMiddleware()],
        invoke_fn=agent.core_invoke,
    )
    result = await pipeline.run(ctx)
"""

from src.app.core.middleware.error_handling_middleware import ErrorHandlingMiddleware
from src.app.core.middleware.guardrail_middleware import GuardrailMiddleware
from src.app.core.middleware.logging_middleware import LoggingMiddleware
from src.app.core.middleware.memory_middleware import MemoryMiddleware
from src.app.core.middleware.pipeline import AgentPipeline, MiddlewareManager
from src.app.core.middleware.types import AgentContext, AgentMiddleware, InvokeResult, NextFn, build_invoke_config
