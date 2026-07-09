"""Generate a one-line semantic description of an ingested file (the map's description, #23).

Runs once per file at ingest time (only for new/changed files), using the cheap long-term-memory
model. The description lets the session-start briefing tell the agent *what each file is* so it can
decide whether to open it — without reading the whole corpus every session.
"""

from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.app.core.common.config import settings
from src.app.core.common.logging import logger

# Cap the input: a one-liner needs only the head of the document, and it keeps the call cheap.
_MAX_INPUT_CHARS = 4000
_MAX_DESCRIPTION_CHARS = 300

_SYSTEM_PROMPT = (
    "Você descreve arquivos para um catálogo. Dado o nome e um trecho do conteúdo, escreva UMA "
    "frase curta (máx. 25 palavras), em português, dizendo objetivamente o que o arquivo contém — "
    "para alguém decidir se precisa abri-lo. Sem preâmbulo, sem 'este arquivo', sem aspas."
)

_describer = ChatOpenAI(model=settings.LONG_TERM_MEMORY_MODEL, api_key=settings.OPENAI_API_KEY)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _summarize(title: str, text: str) -> str:
    """One-line description via the cheap model, retrying transient failures."""
    response = await _describer.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Nome: {title}\n\nTrecho:\n{text}"},
        ]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return content.strip().replace("\n", " ")[:_MAX_DESCRIPTION_CHARS]


async def describe_file(title: str, text: str) -> str:
    """Return a short PT-BR description of a file from its extracted text; ``""`` on empty/failure.

    Never raises — a description failure must not abort ingestion (the file is still ingested and
    searchable; it just lacks a catalog blurb, which a later sync can fill).
    """
    if not text or not text.strip():
        return ""
    try:
        return await _summarize(title, text[:_MAX_INPUT_CHARS])
    except Exception:
        logger.exception("describe_file_failed", title=title)
        return ""
