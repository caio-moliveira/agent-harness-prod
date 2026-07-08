"""Document-layer tools for the Data Agent: ``list_documents`` and ``read_document``.

These complement (never replace) the deepagents filesystem built-ins. They operate on the *ingested
manifest* — a stable ``doc_id`` catalog with page counts and text-layer state — instead of the raw
live filesystem:

- ``list_documents`` reads ONLY the manifest (never touches disk): the id circulates between tools,
  the human-readable title is display-only and is never accepted as a parameter.
- ``read_document`` opens an explicit, mandatory page range from the ingested text. There is no
  whole-document read: to see everything the agent paginates (and feels the cost). It never
  truncates silently — a partial read reports the exact next range to request.

Both are scoped to one ``(user_id, agent_id)`` corpus, the same isolation the rest of the product
enforces. Reading records the pages into the session's read set — the basis for citation checks.
"""

import asyncio
import base64
import os
import re
import tempfile
from itertools import groupby
from typing import Annotated, List, Optional

import pymupdf
from langchain_core.messages import ToolMessage
from langchain_core.messages.content import create_image_block
from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.normalize import normalize_text
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.sandbox.paths import is_within_allowed_roots
from src.app.core.sandbox.registry import registry

# Catalog cap and read budget. The read budget is a rough char proxy for a token ceiling — small
# enough that reading a range is cheap, large enough for a few dense pages.
_MAX_LIST = 50
_MAX_READ_CHARS = 6000
# Literal-search caps: how many hits to return, and the context padding around each match.
_MAX_HITS = 20
_EXCERPT_PAD = 45
# Page-image rasterization: DPI (legible without being huge) + an on-disk cache. The cache key uses
# the content-addressed doc_id, so a changed document (new doc_id) never serves a stale image.
_PAGE_IMAGE_DPI = 150
_PAGE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "data_agent_page_cache")


def _render_page_png(pdf_path: str, page_index: int, doc_id: str) -> bytes:
    """Rasterize one PDF page to PNG bytes, caching the result on disk (blocking — run in a thread)."""
    cache_file = os.path.join(_PAGE_CACHE_DIR, f"{doc_id}_{page_index}_{_PAGE_IMAGE_DPI}.png")
    if os.path.isfile(cache_file):
        with open(cache_file, "rb") as f:
            return f.read()
    doc = pymupdf.open(pdf_path)
    try:
        png = doc[page_index].get_pixmap(dpi=_PAGE_IMAGE_DPI).tobytes("png")
    finally:
        doc.close()
    os.makedirs(_PAGE_CACHE_DIR, exist_ok=True)
    with open(cache_file, "wb") as f:
        f.write(png)
    return png

# A printed folio is usually a bare number (optionally dash-wrapped) on the first or last line.
_FOLIO_RE = re.compile(r"^\s*[-–—]?\s*(\d{1,4})\s*[-–—]?\s*$")


def _detect_folio(text: str) -> Optional[int]:
    """Best-effort printed page number from a page's text (header/footer), or None."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    for line in (lines[-1], lines[0]):  # folio usually sits at the bottom, sometimes the top
        m = _FOLIO_RE.match(line)
        if m:
            return int(m.group(1))
    return None


def _reassemble_pages(chunks) -> List[dict]:
    """Rebuild ordered pages from a document's chunks (split-page pieces re-joined by section)."""
    pages: List[dict] = []
    for c in chunks:
        needs_ocr = bool((c.meta or {}).get("needs_ocr", False))
        if pages and pages[-1]["label"] == c.section:
            if c.content:
                pages[-1]["text"] = (pages[-1]["text"] + "\n" + c.content).strip()
        else:
            pages.append({"label": c.section, "text": c.content or "", "needs_ocr": needs_ocr})
    return pages


def _page_header(pdf_index: int, total: int, text: str, needs_ocr: bool) -> str:
    """Build the per-page header carrying the PDF index and the printed folio (with divergence)."""
    folio = _detect_folio(text)
    header = f"=== PDF pág. {pdf_index}/{total}"
    if folio is not None:
        header += f" · fólio impresso {folio}"
        if folio != pdf_index:
            header += " ⚠ divergente"
    elif needs_ocr:
        header += " · sem texto (provável página escaneada)"
    return header + " ==="


def _excerpt(original: str, approx_pos: int, qlen: int) -> str:
    """A short one-line context window around a match, taken from the original (accented) text."""
    start = max(0, approx_pos - _EXCERPT_PAD)
    end = min(len(original), approx_pos + qlen + _EXCERPT_PAD)
    snippet = original[start:end].replace("\n", " ").strip()
    return f"{'…' if start > 0 else ''}{snippet}{'…' if end < len(original) else ''}"


def make_document_tools(user_id: Optional[int], agent_id: Optional[int], session_id: Optional[str]) -> List[BaseTool]:
    """Build ``list_documents`` + ``read_document`` bound to one (user, agent) corpus. Empty if no user."""
    if user_id is None:
        return []
    manifest = IngestedFileRepository()
    chunks_repo = DocumentChunkRepository()

    @tool
    async def list_documents() -> str:
        """Cataloga os documentos indexados desta pasta/agente (lê só o manifesto, não abre arquivos).

        Use SEMPRE ANTES de ler ou citar um documento: devolve, por documento, o `doc_id` (identificador
        estável que você passa para `read_document`), o título (só para exibir ao usuário — NUNCA use o
        título como parâmetro), a contagem de páginas e o estado da camada de texto (nativo/ocr/misto).
        Se a lista estiver truncada, o total é declarado.
        """
        docs = await manifest.list_all(user_id, agent_id)
        if not docs:
            return (
                "Nenhum documento indexado para este agente ainda. Peça ao usuário para conectar/atualizar "
                "a pasta em Fontes (a indexação roda ao conceder a pasta)."
            )
        total = len(docs)
        shown = docs[:_MAX_LIST]
        lines = [
            f"- {d.doc_id} · \"{d.title}\" · {d.page_count} pág · texto: {d.text_layer} "
            f"({d.ocr_confidence:.0%} das páginas com texto)"
            for d in shown
        ]
        header = f"{total} documento(s) indexado(s)"
        if total > _MAX_LIST:
            header += f" — mostrando os primeiros {_MAX_LIST} (há {total} no total)"
        return header + ":\n" + "\n".join(lines)

    @tool
    async def read_document(doc_id: str, start_page: int, end_page: int) -> str:
        """Lê um intervalo EXPLÍCITO de páginas (`start_page`..`end_page`) de um documento pelo `doc_id`.

        Não existe leitura do documento inteiro — informe o intervalo. Use o `doc_id` de `list_documents`
        (nunca o título). Para saber QUAIS páginas ler, use antes `buscar_documentos` (que aponta a página).
        A leitura é limitada por um teto: se o intervalo não couber, devolve o que coube e informa o próximo
        intervalo a pedir (nunca trunca em silêncio). Cada página traz o índice do PDF e, quando detectável,
        o fólio impresso — com aviso de divergência entre os dois.
        """
        record = await manifest.get_by_doc_id(user_id, agent_id, doc_id)
        if record is None:
            return f"Documento '{doc_id}' não encontrado. Use list_documents para ver os doc_id disponíveis."
        chunks = await chunks_repo.get_chunks_by_source(user_id, agent_id, record.source_path)
        pages = _reassemble_pages(chunks)
        total = len(pages)
        if total == 0:
            return f"'{record.title}' não tem conteúdo indexado (vazio, ou escaneado sem OCR)."
        if start_page < 1 or end_page < start_page:
            return f"Intervalo inválido. Peça 1 <= start_page <= end_page <= {total} (o documento tem {total} páginas)."
        if start_page > total:
            return f"start_page {start_page} fora do documento — ele tem {total} páginas."

        end = min(end_page, total)
        blocks: List[str] = []
        used = 0
        for idx in range(start_page, end + 1):
            page = pages[idx - 1]
            header = _page_header(idx, total, page["text"], page["needs_ocr"])
            body = page["text"] or "(sem texto extraível nesta página)"
            block = f"{header}\n{body}"
            # A single page that alone blows the budget: return it truncated with an escape hatch.
            if not blocks and len(block) > _MAX_READ_CHARS:
                if session_id:
                    await registry.mark_pages_read(session_id, doc_id, [idx])
                return (
                    block[:_MAX_READ_CHARS]
                    + f"\n\n… [a página {idx} excede o limite de leitura e foi truncada. Refine com "
                    "`buscar_documentos` para o trecho exato.]"
                )
            # Adding this page would exceed the budget and we already have content: stop here.
            if blocks and used + len(block) > _MAX_READ_CHARS:
                included_end = start_page + len(blocks) - 1
                if session_id:
                    await registry.mark_pages_read(session_id, doc_id, range(start_page, included_end + 1))
                return (
                    "\n\n".join(blocks)
                    + f"\n\n… leitura parcial (limite de tokens): li até a página {included_end}. "
                    f"Continue com read_document('{doc_id}', {idx}, {end_page})."
                )
            blocks.append(block)
            used += len(block)

        if session_id:
            await registry.mark_pages_read(session_id, doc_id, range(start_page, end + 1))
        return "\n\n".join(blocks)

    @tool
    async def search_documents(query: str) -> str:
        """Busca LITERAL de um termo exato no texto dos documentos (ignora acento e caixa).

        É a ferramenta certa para termo EXATO: número de processo, CNPJ, artigo, data, valor, nome
        próprio, "Emenda Constitucional nº 100". É a ferramenta ERRADA para conceito parafraseado ou
        pergunta em linguagem natural — para isso use `buscar_documentos` (busca por significado).
        Retorna as coordenadas de cada ocorrência (doc_id, página do PDF, fólio) e um trecho curto —
        NUNCA a página inteira; para ler, chame depois `read_document(doc_id, página, página)`.
        Informa a consulta já normalizada: se voltar vazio, o termo realmente não está no texto
        (não é problema de acento/caixa) — tente sinônimos ou `buscar_documentos`, não repita variações de acento.
        """
        norm_query = normalize_text(query)
        if not norm_query:
            return "Consulta vazia. Informe um termo exato (número, artigo, data, valor ou nome próprio)."
        docs = await manifest.list_all(user_id, agent_id)
        if not docs:
            return "Nenhum documento indexado para este agente ainda."
        by_source = {d.source_path: d for d in docs}
        all_chunks = await chunks_repo.get_chunks(user_id, agent_id)  # ordered by (source_path, chunk_index)

        hits: List[tuple] = []
        truncated = False
        for source_path, group in groupby(all_chunks, key=lambda c: c.source_path):
            doc = by_source.get(source_path)
            if doc is None:
                continue
            for idx, page in enumerate(_reassemble_pages(list(group)), start=1):
                pos = normalize_text(page["text"]).find(norm_query)
                if pos == -1:
                    continue
                hits.append((doc, idx, _detect_folio(page["text"]), _excerpt(page["text"], pos, len(norm_query))))
                if len(hits) >= _MAX_HITS:
                    truncated = True
                    break
            if truncated:
                break

        if not hits:
            return (
                f'Nenhuma ocorrência literal de "{norm_query}" (busca exata, ignorando acento e caixa). '
                "Se for um conceito ou termo parafraseado, use `buscar_documentos` (busca por significado)."
            )
        lines = [
            f'- {doc.doc_id} "{doc.title}" · PDF pág. {idx}'
            f'{f" · fólio {folio}" if folio is not None else ""} · "{excerpt}"'
            for doc, idx, folio, excerpt in hits
        ]
        header = f'Busca literal por "{norm_query}" — {len(hits)} ocorrência(s)'
        if truncated:
            header += f" (mostrando as primeiras {_MAX_HITS})"
        return header + ". Leia a página com read_document(doc_id, pág, pág):\n" + "\n".join(lines)

    @tool
    async def read_page_image(doc_id: str, page: int, tool_call_id: Annotated[str, InjectedToolCallId]):
        """Renderiza uma página de PDF como IMAGEM e a entrega para você VER (não como texto).

        Use como PRIMEIRA escolha — não como plano B — quando o layout carrega significado: tabela
        contábil, coluna de valores, quadro comparativo de licitação, assinatura, carimbo; e sempre
        que o documento estiver como camada de texto `ocr`/baixa confiança (em `list_documents`) ou o
        texto extraído por `read_document` sair ambíguo/embaralhado. `page` é o índice do PDF (o mesmo
        que aparece em `read_document`/`search_documents`). Custa mais que ler texto — use quando a
        imagem realmente ajuda.
        """
        record = await manifest.get_by_doc_id(user_id, agent_id, doc_id)
        if record is None:
            return f"Documento '{doc_id}' não encontrado. Use list_documents para ver os doc_id."
        if not record.source_path.lower().endswith(".pdf"):
            return "read_page_image só rasteriza PDFs. Para este documento use read_document (texto)."
        if page < 1 or page > record.page_count:
            return f"Página {page} fora do documento — ele tem {record.page_count} páginas."
        # Security: re-validate the host path against the allow-list on every use (a tightened
        # SANDBOX_ALLOWED_ROOTS revokes access), and confirm the file still exists.
        if not is_within_allowed_roots(record.source_path, settings.SANDBOX_ALLOWED_ROOTS) or not os.path.isfile(
            record.source_path
        ):
            return "Arquivo indisponível (fora das raízes permitidas agora, ou removido do disco)."
        try:
            png = await asyncio.to_thread(_render_page_png, record.source_path, page - 1, doc_id)
        except Exception as e:  # noqa: BLE001 - never crash the turn on a render failure
            logger.exception("page_image_render_failed", doc_id=doc_id, page=page, error_type=type(e).__name__)
            return f"Falha ao renderizar a página {page} ({type(e).__name__}). Tente read_document (texto)."
        if session_id:
            await registry.mark_pages_read(session_id, doc_id, [page])
        image_b64 = base64.standard_b64encode(png).decode("utf-8")
        return ToolMessage(
            content_blocks=[create_image_block(base64=image_b64, mime_type="image/png")],
            name="read_page_image",
            tool_call_id=tool_call_id,
        )

    return [list_documents, read_document, search_documents, read_page_image]
