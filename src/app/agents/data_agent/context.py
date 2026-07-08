"""Build a workspace 'briefing' injected into the agent's system prompt at session start.

So the agent is always grounded in the sources it was given — the granted folder and/or the
connected database — without having to decide to call a tool first. Analogous to reading a
project ``AGENTS.md``/``CONTEXT.md`` at the start of every session.
"""

import os
from typing import Any, Optional

# Conventional in-folder context files, in priority order — the first that exists is read in full.
_CONTEXT_FILENAMES = ("AGENTS.md", "CONTEXT.md", "README.md")
_MAX_FILES = 50
_MAX_CONTEXT_FILE_CHARS = 2500
_MAX_DB_INFO_CHARS = 3000


def _folder_brief(folder: str, docs: Optional[list] = None) -> Optional[str]:
    """Brief the granted folder: the INDEXED document catalog + a conventional context file.

    When ``docs`` (the ingested manifest) is provided, the listing reflects exactly what the
    document tools can search — never a raw disk file that isn't indexed yet (which is what made the
    agent "see" files it then couldn't find anything in). If the manifest is still empty while a
    folder is attached, we say indexing is in progress rather than promising searchable files.
    ``docs=None`` falls back to a plain disk listing (used when no manifest is available).
    """
    if not folder or not os.path.isdir(folder):
        return None

    lines: list[str] = []

    if docs is not None:
        if docs:
            listing = [f"- {d.title} ({d.page_count} págs · texto {d.text_layer})" for d in docs[:_MAX_FILES]]
            lines.append(
                "Documentos indexados em `/workspace` (pesquisáveis com `list_documents`, "
                "`search_documents`, `read_document`):\n" + "\n".join(listing)
            )
        else:
            lines.append(
                "A pasta em `/workspace` foi conectada e seus documentos estão sendo indexados — "
                "em instantes você poderá pesquisá-los com `list_documents`/`search_documents`."
            )
    else:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return None
        listing = []
        for name in names[:_MAX_FILES]:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    listing.append(f"- {name} ({os.path.getsize(path)} bytes)")
                except OSError:
                    listing.append(f"- {name}")
        if listing:
            lines.append("Arquivos disponíveis em `/workspace`:\n" + "\n".join(listing))

    for candidate in _CONTEXT_FILENAMES:
        cpath = os.path.join(folder, candidate)
        if os.path.isfile(cpath):
            try:
                with open(cpath, encoding="utf-8", errors="replace") as f:
                    text = f.read(_MAX_CONTEXT_FILE_CHARS + 1)
            except OSError:
                break
            truncated = text[:_MAX_CONTEXT_FILE_CHARS]
            suffix = " …(truncado)" if len(text) > _MAX_CONTEXT_FILE_CHARS else ""
            lines.append(f"Conteúdo de `{candidate}`:\n{truncated}{suffix}")
            break

    return "\n\n".join(lines) if lines else None


def _db_brief(db: Any) -> Optional[str]:
    """Return a compact schema (tables, columns, sample rows) for the connected database."""
    if db is None:
        return None
    try:
        info = db.get_table_info()
    except Exception:  # noqa: BLE001
        return None
    if not info:
        return None
    truncated = info[:_MAX_DB_INFO_CHARS]
    suffix = " …(truncado)" if len(info) > _MAX_DB_INFO_CHARS else ""
    return f"Esquema do banco de dados conectado:\n{truncated}{suffix}"


def build_workspace_context(folder: Optional[str], db: Any, docs: Optional[list] = None) -> str:
    """Assemble the workspace briefing from the granted folder and/or connected database.

    ``docs`` is the ingested document manifest (list of ``IngestedFile``); when given, the folder
    brief lists the indexed, searchable documents instead of raw disk files. Returns an empty string
    when no source is attached (nothing to prime).
    """
    parts = [p for p in (_folder_brief(folder, docs) if folder else None, _db_brief(db)) if p]
    if not parts:
        return ""
    return (
        "## Contexto do workspace (carregado no início da sessão)\n\n"
        "Você JÁ tem acesso às fontes abaixo. Baseie suas respostas nelas; para ler um arquivo "
        "(inclusive PDF, Word e Excel) use `read_file`, para localizar um trecho específico use "
        "`buscar_documentos`, e para consultar o banco use as ferramentas SQL.\n\n"
        + "\n\n".join(parts)
    )
