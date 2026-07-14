"""Open Deep Research agent module.

This package contains the Deep Research agent that conducts multi-step research
using a supervisor-researcher architecture with LangGraph subgraphs.
"""

from langchain.chat_models import init_chat_model

from src.app.agents.tools.search_tool import SearchAPI
from src.app.core.common.config import settings
from src.app.core.common.utils import get_api_key_for_model

# ──────────────────────────────────────
# Deep Research Agent Configuration
# ──────────────────────────────────────

# General
DEEP_RESEARCH_AGENT_NAME = "Deep Research"
ALLOW_CLARIFICATION = True
MAX_STRUCTURED_OUTPUT_RETRIES = 3

# Research limits — bounded on purpose to cap web-search cost/latency for a delegated task.
# The hard ceiling on web searches per run is: units × iterations × react_calls × queries_per_search.
# With 1 × 1 × 3 × 2 (queries capped in tools/search_tool.py) that is at most 6 searches — enough to
# ground a recommendation without the fan-out blow-up (was 5 × 3 × 10). Keeping a single research unit
# also means exactly one ResearchComplete signal (no parallel-researcher duplication).
MAX_CONCURRENT_RESEARCH_UNITS = 1
MAX_RESEARCHER_ITERATIONS = 1
MAX_REACT_TOOL_CALLS = 3

# Search
SEARCH_API = SearchAPI.TAVILY

# Models — the single configured chat model (``settings.MODEL``, a "provider:model" string consumed by
# init_chat_model). For Azure the base id is the deployment name and endpoint/version are threaded
# through the config dicts below (env-var names differ from what init_chat_model auto-reads).
def _model_config(model: str, max_tokens: int) -> dict:
    """Build the ``.with_config`` payload, adding Azure endpoint/version when the model is on Azure."""
    config: dict = {"model": model, "max_tokens": max_tokens, "api_key": get_api_key_for_model(model)}
    if model.startswith(("azure_openai:", "azure:")):
        config["azure_endpoint"] = settings.AZURE_OPENAI_ENDPOINT
        config["api_version"] = settings.AZURE_OPENAI_API_VERSION
    return config


RESEARCH_MODEL = settings.MODEL
RESEARCH_MODEL_MAX_TOKENS = 10000

COMPRESSION_MODEL = settings.MODEL
COMPRESSION_MODEL_MAX_TOKENS = 8192

FINAL_REPORT_MODEL = settings.MODEL
FINAL_REPORT_MODEL_MAX_TOKENS = 10000


MAX_CONTENT_LENGTH = 50000

# Shared configurable model used across all subgraphs. Azure needs endpoint/version to be
# configurable too (init_chat_model's auto env-read uses different var names than our settings).
configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key", "azure_endpoint", "api_version"),
)

writer_model_config = _model_config(FINAL_REPORT_MODEL, FINAL_REPORT_MODEL_MAX_TOKENS)

research_model_config = _model_config(RESEARCH_MODEL, RESEARCH_MODEL_MAX_TOKENS)

compress_model_config = _model_config(COMPRESSION_MODEL, COMPRESSION_MODEL_MAX_TOKENS)
