"""Application configuration management.

This module handles environment-specific configuration loading, parsing, and management
for the application. It includes environment detection, .env file loading, and
configuration value parsing.
"""

import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv


# Define environment types
class Environment(str, Enum):
    """Application environment types.

    Defines the possible environments the application can run in:
    development, staging, production, and test.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


# Determine environment
def get_environment() -> Environment:
    """Get the current environment.

    Returns:
        Environment: The current environment (development, staging, production, or test)
    """
    match os.getenv("APP_ENV", "development").lower():
        case "production" | "prod":
            return Environment.PRODUCTION
        case "staging" | "stage":
            return Environment.STAGING
        case "test":
            return Environment.TEST
        case _:
            return Environment.DEVELOPMENT


# Load appropriate .env file based on environment
def load_env_file():
    """Load environment-specific .env file."""
    env = get_environment()
    print(f"Loading environment: {env}")
    # Project root: config.py lives at <root>/src/app/core/common/config.py
    base_dir = str(Path(__file__).resolve().parents[4])

    # Define env files in priority order
    env_files = [
        os.path.join(base_dir, f".env.{env.value}.local"),
        os.path.join(base_dir, f".env.{env.value}"),
        os.path.join(base_dir, ".env.local"),
        os.path.join(base_dir, ".env"),
    ]

    # Load the first env file that exists
    for env_file in env_files:
        if os.path.isfile(env_file):
            load_dotenv(dotenv_path=env_file)
            print(f"Loaded environment from {env_file}")
            return env_file

    # Fall back to default if no env file found
    return None


ENV_FILE = load_env_file()


# Parse list values from environment variables
def parse_list_from_env(env_key, default=None):
    """Parse a comma-separated list from an environment variable."""
    value = os.getenv(env_key)
    if not value:
        return default or []

    # Remove quotes if they exist
    value = value.strip("\"'")
    # Handle single value case
    if "," not in value:
        return [value]
    # Split comma-separated values
    return [item.strip() for item in value.split(",") if item.strip()]


# Parse dict of lists from environment variables with prefix
def parse_dict_of_lists_from_env(prefix, default_dict=None):
    """Parse dictionary of lists from environment variables with a common prefix."""
    result = default_dict or {}

    # Look for all env vars with the given prefix
    for key, value in os.environ.items():
        if key.startswith(prefix):
            endpoint = key[len(prefix) :].lower()  # Extract endpoint name
            # Parse the values for this endpoint
            if value:
                value = value.strip("\"'")
                if "," in value:
                    result[endpoint] = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    result[endpoint] = [value]

    return result


class Settings:
    """Application settings without using pydantic."""

    def __init__(self):
        """Initialize application settings from environment variables.

        Loads and sets all configuration values from environment variables,
        with appropriate defaults for each setting. Also applies
        environment-specific overrides based on the current environment.
        """
        # Set the environment
        self.ENVIRONMENT = get_environment()

        # Application Settings
        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "Agentic prod ready template")
        self.VERSION = os.getenv("VERSION", "1.0.0")
        self.DESCRIPTION = os.getenv(
            "DESCRIPTION", "A production-ready agent template with LangGraph and Langfuse integration"
        )
        self.API_V1_STR = os.getenv("API_V1_STR", "/api/v1")
        self.DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "t", "yes")

        # CORS Settings
        self.ALLOWED_ORIGINS = parse_list_from_env("ALLOWED_ORIGINS", ["*"])

        # Langfuse Configuration
        self.LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # LangGraph Configuration
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        self.DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "gpt-5-mini")
        self.DEFAULT_LLM_TEMPERATURE = float(os.getenv("DEFAULT_LLM_TEMPERATURE", "0.2"))
        self.MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
        self.MAX_LLM_CALL_RETRIES = int(os.getenv("MAX_LLM_CALL_RETRIES", "3"))

        # Chat-model provider. The agents' chat models are built by src/app/core/llm/factory.py from
        # these settings; long-term memory (mem0) and evals keep their own OpenAI models. Anthropic
        # (Claude Sonnet 5) is the default so prompt caching is available out of the box; set
        # LLM_PROVIDER=openai to keep the previous OpenAI behavior.
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        self.ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
        # Anthropic requires an explicit output cap. Streaming is used on the hot paths, so a
        # generous default leaves room for large deliverables without HTTP timeouts.
        self.ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192"))
        # Thinking mode for Anthropic: "disabled" (default) or "adaptive". Sonnet 5 turns adaptive
        # thinking ON when the param is omitted, which emits thinking blocks that must be echoed
        # back verbatim inside a tool loop — langchain_anthropic mis-serializes them during
        # streaming and the API 400s ("thinking.thinking: Field required"). Disabling avoids the
        # bug and cuts token/latency cost; flip to "adaptive" only if that replay path is fixed.
        self.ANTHROPIC_THINKING = os.getenv("ANTHROPIC_THINKING", "disabled").lower()
        # Prompt caching (prefix match). Deep agents cache via AnthropicPromptCachingMiddleware; the
        # chatbot caches a stable system block. Only meaningful when LLM_PROVIDER=anthropic.
        self.PROMPT_CACHING_ENABLED = os.getenv("PROMPT_CACHING_ENABLED", "true").lower() in ("true", "1", "yes")
        self.PROMPT_CACHE_TTL = os.getenv("PROMPT_CACHE_TTL", "5m")  # "5m" or "1h"

        # Long term memory Configuration
        self.LONG_TERM_MEMORY_MODEL = os.getenv("LONG_TERM_MEMORY_MODEL", "gpt-5-nano")
        self.LONG_TERM_MEMORY_EMBEDDER_MODEL = os.getenv("LONG_TERM_MEMORY_EMBEDDER_MODEL", "text-embedding-3-small")
        self.LONG_TERM_MEMORY_COLLECTION_NAME = os.getenv("LONG_TERM_MEMORY_COLLECTION_NAME", "longterm_memory")
        # JWT Configuration
        self.JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
        self.JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

        # Logging Configuration
        self.LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" or "console"

        # Postgres Configuration
        self.POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
        self.POSTGRES_DB = os.getenv("POSTGRES_DB", "food_order_db")
        self.POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
        self.POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
        self.POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
        self.POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "10"))
        self.CHECKPOINT_TABLES = ["checkpoint_blobs", "checkpoint_writes", "checkpoints"]

        # MCP Configuration
        self.MCP_ENABLED = os.getenv("MCP_ENABLED", "true").lower() in ("true", "1", "yes")
        self.MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "7001"))
        self.MCP_HOSTNAMES = [h.strip() for h in os.getenv("MCP_HOSTNAMES_CSV", "").split(",") if h.strip()]

        # Per-session data sources (DB engine + granted folder). A granted folder is served to
        # the Data Agent's read-only file tools by a per-session deepagents FilesystemBackend
        # (virtual_mode, read-only) rooted at the folder — there is no per-session container.
        self.SESSION_SOURCE_TTL = int(os.getenv("SESSION_SOURCE_TTL", "3600"))
        # LangGraph graph-recursion cap per turn. Above the framework default of 25 so a few
        # tool retries (e.g. correcting a SQL query) don't abort a legitimate multi-step turn.
        self.AGENT_RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "50"))
        self.SANDBOX_ENABLED = os.getenv("SANDBOX_ENABLED", "true").lower() in ("true", "1", "yes")
        # Virtual path the granted folder is exposed at (the CompositeBackend route prefix).
        self.SANDBOX_MOUNT_PATH = os.getenv("SANDBOX_MOUNT_PATH", "/workspace")
        # Allow-list of host roots that may be granted. Empty = deny all grants (secure by
        # default). A granted folder must live under one of these roots.
        self.SANDBOX_ALLOWED_ROOTS = parse_list_from_env("SANDBOX_ALLOWED_ROOTS", [])

        # Application-level secret for encrypting persisted credentials (e.g. a bound database
        # password). Empty = secure-by-default: passwords are NOT persisted at rest and must be
        # re-entered per session. Any non-empty string works (a Fernet key is derived from it).
        self.ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

        # Vetted registry a user may fetch skills from (single allow-listed base URL). Empty =
        # fetch disabled (users can still author skills). Only this host may be fetched.
        self.SKILL_REGISTRY_URL = os.getenv("SKILL_REGISTRY_URL", "https://skillsmp.com/")

        # Rate Limiting Configuration
        self.RATE_LIMIT_DEFAULT = parse_list_from_env("RATE_LIMIT_DEFAULT", ["200 per day", "50 per hour"])

        # Rate limit endpoints defaults
        default_endpoints = {
            "chat": ["30 per minute"],
            "chat_stream": ["20 per minute"],
            "deep_research": ["10 per minute"],
            "deep_research_stream": ["10 per minute"],
            "text_to_sql": ["15 per minute"],
            "data_agent": ["15 per minute"],
            "data_connect": ["10 per minute"],
            "agents": ["60 per minute"],
            "skills": ["60 per minute"],
            "session_events": ["60 per minute"],
            "success_metrics": ["60 per minute"],
            "hitl": ["60 per minute"],
            "messages": ["50 per minute"],
            "session_delete": ["30 per minute"],
            "register": ["10 per hour"],
            "login": ["20 per minute"],
            "root": ["10 per minute"],
            "health": ["20 per minute"],
        }

        # Update rate limit endpoints from environment variables
        self.RATE_LIMIT_ENDPOINTS = default_endpoints.copy()
        for endpoint in default_endpoints:
            env_key = f"RATE_LIMIT_{endpoint.upper()}"
            value = parse_list_from_env(env_key)
            if value:
                self.RATE_LIMIT_ENDPOINTS[endpoint] = value

        # Evaluation Configuration
        self.EVALUATION_LLM = os.getenv("EVALUATION_LLM", "gpt-5")
        self.EVALUATION_BASE_URL = os.getenv("EVALUATION_BASE_URL", "https://api.openai.com/v1")
        self.EVALUATION_API_KEY = os.getenv("EVALUATION_API_KEY", self.OPENAI_API_KEY)
        self.EVALUATION_SLEEP_TIME = int(os.getenv("EVALUATION_SLEEP_TIME", "10"))

        # Apply environment-specific settings
        self.apply_environment_settings()

    def apply_environment_settings(self):
        """Apply environment-specific settings based on the current environment."""
        env_settings = {
            Environment.DEVELOPMENT: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "200 per hour"],
            },
            Environment.STAGING: {
                "DEBUG": False,
                "LOG_LEVEL": "INFO",
                "RATE_LIMIT_DEFAULT": ["500 per day", "100 per hour"],
            },
            Environment.PRODUCTION: {
                "DEBUG": False,
                "LOG_LEVEL": "WARNING",
                "RATE_LIMIT_DEFAULT": ["200 per day", "50 per hour"],
            },
            Environment.TEST: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "1000 per hour"],  # Relaxed for testing
            },
        }

        # Get settings for current environment
        current_env_settings = env_settings.get(self.ENVIRONMENT, {})

        # Apply settings if not explicitly set in environment variables
        for key, value in current_env_settings.items():
            env_var_name = key.upper()
            # Only override if environment variable wasn't explicitly set
            if env_var_name not in os.environ:
                setattr(self, key, value)


# Create settings instance
settings = Settings()
