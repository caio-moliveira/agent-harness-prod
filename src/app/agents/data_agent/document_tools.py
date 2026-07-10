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
import json
import os
import re
import tempfile
from typing import Annotated, List, Optional

import pymupdf
from langchain_core.messages import ToolMessage
from langchain_core.messages.content import create_image_block
from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.ingestion.normalize import normalize_text
from src.app.core.ingestion.source_model import IngestedFileStatus
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


def _strip_ext(name: str) -> str:
    """Filename without its extension (so 'representacao_986639.pdf' matches 'representacao_986639')."""
    return os.path.splitext(name)[0]


def _catalog_lines(docs) -> str:
    """A compact catalog (doc_id + title + pages), echoed in errors so the model self-corrects."""
    return "\n".join(f'- {d.doc_id} · "{d.title}" ({d.page_count} pág)' for d in docs[:_MAX_LIST])


# Extensions whose structure tree locates sections by LINE number (single parsed section) rather
# than by section ordinal (pdf pages / docx blocks / xlsx sheets) — so content serving slices right.
_LINE_BASED_EXTS = {".md", ".txt", ".log", ".json", ".csv", ".tsv"}


def _find_node(nodes: list, node_id: str) -> Optional[dict]:
    """Depth-first search for a node by ``node_id`` in a structure-tree dict list, or ``None``."""
    for n in nodes:
        if n.get("node_id") == node_id:
            return n
        found = _find_node(n.get("nodes", []), node_id)
        if found is not None:
            return found
    return None


def _render_outline(nodes: list, depth: int = 0) -> List[str]:
    """Render a structure tree as indented ``node_id [span] title`` lines (depth-first)."""
    lines: List[str] = []
    for n in nodes:
        start, end = n.get("start_index"), n.get("end_index")
        span = "" if start is None else (f" [{start}]" if end in (start, None) else f" [{start}-{end}]")
        lines.append(f"{'  ' * depth}{n.get('node_id', '')}{span} {n.get('title', '')}".rstrip())
        lines.extend(_render_outline(n.get("nodes", []), depth + 1))
    return lines


def _load_sections(content_json: Optional[str]) -> Optional[List[dict]]:
    """Parse the manifest ``content`` JSON into the list of located sections, or ``None`` if absent."""
    if not content_json:
        return None
    try:
        sections = json.loads(content_json)
    except (ValueError, TypeError):
        return None
    return sections if isinstance(sections, list) else None


def _section_pages(sections: List[dict], ext: str) -> List[dict]:
    """Normalize stored sections into ordered ``{label, text, needs_ocr}`` pages for reading/search.

    For line-located single-section types (md/txt/csv) the one blob is returned as a single page;
    for section-ordinal types (pdf/docx/xlsx) each stored section is one page.
    """
    if ext in _LINE_BASED_EXTS and sections:
        s = sections[0]
        return [{"label": s.get("location", "conteúdo"), "text": s.get("text", ""), "needs_ocr": s.get("needs_ocr", False)}]
    return [
        {"label": s.get("location", ""), "text": s.get("text", ""), "needs_ocr": s.get("needs_ocr", False)}
        for s in sections
    ]


def _slice_span(sections: List[dict], ext: str, start: int, end: int) -> str:
    """Return the text spanned by ``[start, end]`` — line range (md/txt/csv) or section range."""
    if ext in _LINE_BASED_EXTS:
        lines = (sections[0].get("text", "") if sections else "").splitlines()
        return "\n".join(lines[start - 1 : end]).strip()
    return "\n\n".join(
        f"=== {s.get('location', '')} ===\n{s.get('text', '')}".strip() for s in sections[start - 1 : end]
    ).strip()


def make_document_tools(user_id: Optional[int], agent_id: Optional[int], session_id: Optional[str]) -> List[BaseTool]:
    """Build ``list_documents`` + ``read_document`` bound to one (user, agent) corpus. Empty if no user."""
    if user_id is None:
        return []
    manifest = IngestedFileRepository()

    async def _resolve_doc(ref: str):
        """Resolve a model-supplied document reference tolerantly.

        Models rarely copy an opaque hash id verbatim, so we accept: the exact ``doc_id``, the id
        with/without the ``doc_`` prefix, the filename/title (extension optional, accent/case-
        insensitive), or a unique partial match. Returns ``(record, None)`` on a unique hit;
        otherwise ``(None, message)`` where the message lists the catalog so the model can
        self-correct in ONE step.
        """
        ref_s = (ref or "").strip()
        docs = await manifest.list_all(user_id, agent_id)
        if not docs:
            return None, "Nenhum documento indexado para este agente ainda. (Conecte/atualize a pasta em Fontes.)"
        # 1) doc_id, tolerating a missing/extra "doc_" prefix.
        id_variants = {ref_s, f"doc_{ref_s}"}
        if ref_s.startswith("doc_"):
            id_variants.add(ref_s[len("doc_") :])
        by_id = [d for d in docs if d.doc_id in id_variants]
        if len(by_id) == 1:
            return by_id[0], None
        # 2) filename / title (extension optional, normalized), then a unique partial match.
        nref = normalize_text(_strip_ext(ref_s))
        if nref:
            by_title = [d for d in docs if normalize_text(_strip_ext(d.title)) == nref]
            if len(by_title) == 1:
                return by_title[0], None
            partial = [d for d in docs if nref in normalize_text(d.title) or nref in d.doc_id.lower()]
            if len(partial) == 1:
                return partial[0], None
            if len(partial) > 1:
                return None, f'"{ref_s}" é ambíguo — use o doc_id exato:\n' + _catalog_lines(partial)
        return None, (
            f'Documento "{ref_s}" não encontrado. Use um destes doc_id (exato) ou o nome do arquivo:\n'
            + _catalog_lines(docs)
        )

    @tool
    async def list_documents() -> str:
        """Cataloga os documentos indexados desta pasta/agente (lê só o manifesto, não abre arquivos).

        Use SEMPRE ANTES de ler ou citar um documento: devolve, por documento, o `doc_id` (identificador
        estável que você passa para `read_document`), o título (só para exibir ao usuário — NUNCA use o
        título como parâmetro), a contagem de páginas e o estado da camada de texto (nativo/ocr/misto).
        Se a lista estiver truncada, o total é declarado.
        """
        docs = await manifest.list_all(user_id, agent_id)
        # The map: only active files are readable; deleted rows are kept only so the agent knows a
        # file it saw before is gone (not an error to retry).
        active = [d for d in docs if d.status != IngestedFileStatus.DELETED]
        if not active:
            return (
                "Nenhum documento indexado para este agente ainda. Peça ao usuário para conectar/atualizar "
                "a pasta em Fontes (a indexação roda ao conceder a pasta)."
            )
        total = len(active)
        shown = active[:_MAX_LIST]
        lines = []
        for d in shown:
            desc = f" — {d.description}" if d.description else ""
            lines.append(
                f"- {d.doc_id} · \"{d.title}\" · {d.page_count} pág · texto: {d.text_layer} "
                f"({d.ocr_confidence:.0%} das páginas com texto){desc}"
            )
        header = f"{total} documento(s) indexado(s)"
        if total > _MAX_LIST:
            header += f" — mostrando os primeiros {_MAX_LIST} (há {total} no total)"
        out = header + ":\n" + "\n".join(lines)
        deleted = [d for d in docs if d.status == IngestedFileStatus.DELETED]
        if deleted:
            out += "\n\nRemovidos da pasta (não leia — não existem mais): " + ", ".join(
                d.title for d in deleted[:_MAX_LIST]
            )
        return out

    @tool
    async def read_document(doc_id: str, start_page: int, end_page: int) -> str:
        """Lê um intervalo EXPLÍCITO de páginas (`start_page`..`end_page`) de um documento pelo `doc_id`.

        Não existe leitura do documento inteiro — informe o intervalo. Identifique o documento pelo
        `doc_id` de `list_documents`/`search_documents` (ou pelo nome do arquivo — ambos funcionam).
        Para saber QUAIS páginas ler, use antes `search_documents` (termo exato) ou `get_document_structure`.
        A leitura é limitada por um teto: se o intervalo não couber, devolve o que coube e informa o próximo
        intervalo a pedir (nunca trunca em silêncio). Cada página traz o índice do PDF e, quando detectável,
        o fólio impresso — com aviso de divergência entre os dois.
        """
        record, err = await _resolve_doc(doc_id)
        if record is None:
            return err
        doc_id = record.doc_id  # canonicalize so page-tracking + the "continue" hint use the real id
        sections = _load_sections(record.content)
        if sections is None:
            return f"'{record.title}' não tem conteúdo indexado. Peça para reindexar a pasta em Fontes."
        pages = _section_pages(sections, os.path.splitext(record.source_path)[1].lower())
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
                    + f"\n\n… [a página {idx} excede o limite de leitura e foi truncada. Navegue por "
                    "`get_document_structure` → `get_node_content` para a seção exata.]"
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
        próprio, "Emenda Constitucional nº 100". Para um conceito parafraseado ou pergunta em
        linguagem natural, navegue pela estrutura (`get_document_structure` → `get_node_content`).
        Retorna as coordenadas de cada ocorrência (doc_id, página do PDF, fólio) e um trecho curto —
        NUNCA a página inteira; para ler, chame depois `read_document(doc_id, página, página)`.
        Informa a consulta já normalizada: se voltar vazio, o termo realmente não está no texto (não é
        problema de acento/caixa) — tente sinônimos ou navegue por `get_document_structure`.
        """
        norm_query = normalize_text(query)
        if not norm_query:
            return "Consulta vazia. Informe um termo exato (número, artigo, data, valor ou nome próprio)."
        docs = await manifest.list_all(user_id, agent_id)
        active = [d for d in docs if d.status != IngestedFileStatus.DELETED]
        if not active:
            return "Nenhum documento indexado para este agente ainda."

        hits: List[tuple] = []
        truncated = False
        for doc in active:
            sections = _load_sections(doc.content)
            if sections is None:
                continue
            for idx, page in enumerate(_section_pages(sections, os.path.splitext(doc.source_path)[1].lower()), start=1):
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
                "Se for um conceito, navegue a estrutura com `get_document_structure` → `get_node_content`."
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
        record, err = await _resolve_doc(doc_id)
        if record is None:
            return err
        doc_id = record.doc_id  # canonicalize for page-read tracking / cache key
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

    @tool
    async def get_document_structure(doc_id: str) -> str:
        """Mostra o ÍNDICE (árvore de seções) de um documento pelo `doc_id`: node_id, faixa e título.

        Primeiro passo para navegar um documento longo: em vez de ler tudo, veja a estrutura e leia só
        a seção relevante com `get_node_content(doc_id, node_id)`. Cada linha traz o `node_id` (que você
        passa adiante), a faixa (páginas do PDF, ou linhas), e o título da seção. Identifique o documento
        pelo `doc_id` de `list_documents` (ou pelo nome do arquivo).
        """
        record, err = await _resolve_doc(doc_id)
        if record is None:
            return err
        if not record.structure:
            return (
                f"'{record.title}' ainda não tem árvore de estrutura indexada "
                "(indexado antes deste recurso, ou sem seções). Use read_document(doc_id, pág, pág)."
            )
        try:
            tree = json.loads(record.structure)
        except (ValueError, TypeError):
            return f"Estrutura de '{record.title}' ilegível. Use read_document(doc_id, pág, pág)."
        outline = _render_outline(tree.get("structure", []))
        if not outline:
            return f"'{record.title}' não tem seções detectadas (documento plano). Use read_document."
        return (
            f'Estrutura de "{record.title}" (doc_id {record.doc_id}) — leia uma seção com '
            f"get_node_content('{record.doc_id}', node_id):\n" + "\n".join(outline)
        )

    @tool
    async def get_node_content(doc_id: str, node_id: str) -> str:
        """Lê o conteúdo de UMA seção do documento, identificada pelo `node_id` de `get_document_structure`.

        Devolve o texto daquela seção (a faixa de páginas/linhas do nó), servido do índice — sem
        reabrir o arquivo. Fluxo: `get_document_structure(doc_id)` → escolha o `node_id` →
        `get_node_content(doc_id, node_id)`. Limitado por um teto: se a seção não couber, devolve o
        começo e avisa (não trunca em silêncio).
        """
        record, err = await _resolve_doc(doc_id)
        if record is None:
            return err
        sections = _load_sections(record.content)
        if not record.structure or sections is None:
            return f"'{record.title}' não tem conteúdo indexado. Peça para reindexar a pasta em Fontes."
        try:
            tree = json.loads(record.structure)
        except (ValueError, TypeError):
            return f"Estrutura de '{record.title}' ilegível. Peça para reindexar a pasta em Fontes."
        node = _find_node(tree.get("structure", []), node_id)
        if node is None:
            return f"node_id '{node_id}' não existe em '{record.title}'. Veja get_document_structure(doc_id)."
        start, end = node.get("start_index"), node.get("end_index")
        if start is None:
            return f"O nó '{node.get('title', '')}' não tem conteúdo direto (ex.: coluna — use consultar_dados)."

        ext = os.path.splitext(record.source_path)[1].lower()
        body = _slice_span(sections, ext, start, end)
        if not body:
            return f"Seção '{node.get('title', '')}' sem texto extraível (pode ser página escaneada)."

        truncated = len(body) > _MAX_READ_CHARS
        body = body[:_MAX_READ_CHARS] if truncated else body
        # Record the pages into the session read set (citation basis) when the locator is page-based.
        if session_id and ext == ".pdf":
            await registry.mark_pages_read(session_id, record.doc_id, range(start, end + 1))
        suffix = "\n\n… [seção truncada no limite de leitura — leia as subseções separadamente]" if truncated else ""
        return f"=== {node.get('title', '')} (doc {record.doc_id}, nó {node_id}) ===\n{body}{suffix}"

    return [
        list_documents,
        read_document,
        search_documents,
        read_page_image,
        get_document_structure,
        get_node_content,
    ]
