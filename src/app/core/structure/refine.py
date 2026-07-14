"""LLM refinement of PDF heading candidates — the one reasoning step in the tree builder.

Heuristics over-propose (a signature line looks like a heading). This makes a single structured LLM
call per document, asking which candidates are real sections and at what level, and keeps only
those. It never raises: on failure it falls back to the heuristic candidates so ingestion proceeds.
Like the catalog describer, it runs at ingest time (background), so it is not attached to a
per-request Langfuse trace.
"""

from typing import List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from src.app.core.common.logging import logger
from src.app.core.llm.factory import create_chat_model
from src.app.core.structure.models import Candidate, RawHeading

_SYSTEM_PROMPT = (
    "Você organiza a estrutura de um documento. Recebe uma lista numerada de linhas candidatas a "
    "título de seção, extraídas por heurística (com ruído: assinaturas, rodapés e fragmentos de "
    "frase podem aparecer). Para CADA candidata, decida se é um título de seção real e seu nível "
    "(1 = seção principal, 2 = subseção). Descarte assinaturas, nomes de pessoas, datas soltas e "
    "fragmentos de frase. Responda com uma decisão por candidata, na ordem recebida."
)


class HeadingDecision(BaseModel):
    """The refiner's verdict on one candidate line."""

    index: int = Field(description="índice da candidata, conforme numerado no prompt")
    is_section: bool = Field(description="True se for um título de seção real")
    level: int = Field(default=1, description="1 = seção principal, 2 = subseção")
    title: str = Field(default="", description="título limpo (sem numeração/ruído), se aplicável")


class RefineResult(BaseModel):
    """Structured output: one decision per candidate."""

    decisions: List[HeadingDecision] = Field(default_factory=list)


_model: Optional[BaseChatModel] = None


def _structured_model() -> BaseChatModel:
    """Lazily build the structured-output model (so importing this module needs no API key)."""
    global _model
    if _model is None:
        _model = create_chat_model().with_structured_output(RefineResult)
    return _model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _classify(candidates: List[Candidate], doc_name: str) -> RefineResult:
    """One structured call classifying every candidate; retries transient failures."""
    listing = "\n".join(f"{c.index}: {c.text}" for c in candidates)
    prompt = f"Documento: {doc_name}\n\nCandidatas:\n{listing}"
    return await _structured_model().ainvoke(
        [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )


async def refine_headings(candidates: List[Candidate], doc_name: str) -> List[RawHeading]:
    """Keep only the candidates the model judges real sections; fall back to heuristics on failure."""
    if not candidates:
        return []
    try:
        result = await _classify(candidates, doc_name)
    except Exception:  # noqa: BLE001 - a refine failure must not abort ingestion
        logger.exception("heading_refine_failed", doc=doc_name, candidates=len(candidates))
        return [RawHeading(title=c.text, level=c.level, start=c.page) for c in candidates]

    kept: List[RawHeading] = []
    for d in result.decisions:
        if d.is_section and 0 <= d.index < len(candidates):
            candidate = candidates[d.index]
            kept.append(RawHeading(title=(d.title or candidate.text).strip(), level=max(1, d.level), start=candidate.page))
    return kept
