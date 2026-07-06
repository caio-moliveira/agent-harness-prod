import os

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.agent import AgentRepository
from src.app.core.db.database import database_factory
from src.app.core.hitl import HitlService, PendingActionRepository
from src.app.core.hitl.executors import register_default_executors
from src.app.core.mcp.session_manager import get_mcp_session_manager
from src.app.core.session import SessionRepository
from src.app.core.session.event_repository import SessionEventRepository
from src.app.core.skill import SkillRepository
from src.app.core.user import UserRepository

dbsession = database_factory.get_session_maker()
user_repository =  UserRepository(dbsession)
session_repository = SessionRepository(dbsession)
agent_repository = AgentRepository(dbsession)
skill_repository = SkillRepository(dbsession)
session_event_repository = SessionEventRepository()
pending_action_repository = PendingActionRepository()
hitl_service = HitlService(pending_action_repository)
register_default_executors()


def langfuse_init():
    # Langfuse is optional observability. Missing/invalid credentials must degrade
    # gracefully (log a warning) rather than crash application startup.
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        logger.warning("langfuse_disabled_no_credentials")
        return

    try:
        langfuse = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )

        if langfuse.auth_check():
            logger.info("langfuse_auth_success")
        else:
            logger.warning("langfuse_auth_failure")
    except Exception as e:
        logger.warning("langfuse_init_skipped", error=str(e))


def get_langfuse_callback_handler() -> CallbackHandler:
    """Create a Langfuse CallbackHandler for tracking LLM interactions.

    Returns:
        CallbackHandler: Configured Langfuse callback handler.
    """

    return CallbackHandler()


async def mcp_dependencies_init():
    if settings.MCP_ENABLED and settings.MCP_HOSTNAMES:
        mcp_manager = get_mcp_session_manager()
        try:
            resource = await mcp_manager.initialize()
            logger.info("mcp_initialized", tool_count=len(resource.tools), session_count=len(resource.sessions))
        except Exception as e:
            logger.error("mcp_initialization_failed", error=str(e))
            logger.warning("continuing_without_mcp_tools")
    else:
        logger.info("mcp_disabled_or_no_hosts_configured")


async def mcp_dependencies_cleanup():
    if settings.MCP_ENABLED and settings.MCP_HOSTNAMES:
        mcp_manager = get_mcp_session_manager()
        await mcp_manager.cleanup()
        logger.info("mcp_cleaned_up")
    else:
        logger.info("mcp_cleanup_skipped")


langfuse_callback_handler = get_langfuse_callback_handler()