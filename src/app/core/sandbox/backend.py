"""Per-session, read-only filesystem backend for the Data Agent's file tools.

Replaces the former per-session Docker container. The agent's built-in file tools
(``ls``/``read_file``/``glob``/``grep``) are served by a deepagents ``FilesystemBackend``
rooted at the session's granted folder, exposed under a virtual ``/workspace`` prefix via a
``CompositeBackend``. The granted folder is strictly READ-ONLY — ``write``/``edit``/``upload``
are denied by a ``ReadOnlyBackend`` wrapper, replicating the old read-only bind mount — and no
``execute``/shell tool is exposed (the backend is not a ``SandboxBackendProtocol``, so
``FilesystemMiddleware`` drops the ``execute`` tool automatically).

Isolation is per session: the factory resolves the authorized root directory from the
invocation's ``config["configurable"]`` (threaded per session in ``DataAgent``), never from
shared/global state, so one session can never resolve another session's folder. Path traversal
(``..``, ``~``, absolute paths outside the root) is blocked by ``virtual_mode=True``.
"""

import os
from collections import OrderedDict
from typing import Any, Optional

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import (
    BackendFactory,
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GrepMatch,
    WriteResult,
)

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.ingestion.parsers import extract_document

# Config key that carries the session's authorized root directory to the backend factory.
# Lives under ``config["configurable"]`` (not ``metadata``) so it is never surfaced as Langfuse
# trace metadata — a granted host path is not written to traces or checkpoints.
ROOT_DIR_CONFIG_KEY = "data_agent_root_dir"

_READ_ONLY_ERROR = (
    "permission_denied: the granted folder is mounted read-only. "
    "Writing, editing, or uploading files is not allowed."
)


class ReadOnlyBackend(BackendProtocol):
    """Wraps a backend so every mutating operation is denied (a read-only view).

    Read operations (``ls``/``read``/``grep``/``glob``/``download``) delegate to the wrapped
    backend; mutating operations (``write``/``edit``/``upload``) return a permission-denied result
    instead of touching disk. This enforces read-only at the backend layer regardless of which
    file tools happen to be exposed — equivalent to the old container's read-only bind mount.

    Async variants are inherited from ``BackendProtocol`` (each delegates to the matching sync
    method), so denials and delegations hold on both the sync and async tool paths.
    """

    def __init__(self, inner: BackendProtocol) -> None:
        """Wrap ``inner``, exposing only its read operations."""
        self._inner = inner

    # --- read operations: delegate to the wrapped backend ---
    def ls_info(self, path: str) -> list[FileInfo]:
        """Delegate directory listing to the wrapped backend."""
        return self._inner.ls_info(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Delegate file read to the wrapped backend."""
        return self._inner.read(file_path, offset=offset, limit=limit)

    def grep_raw(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None) -> list[GrepMatch] | str:
        """Delegate content search to the wrapped backend."""
        return self._inner.grep_raw(pattern, path=path, glob=glob)

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Delegate glob matching to the wrapped backend."""
        return self._inner.glob_info(pattern, path=path)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Delegate file download (used to read image files) to the wrapped backend."""
        return self._inner.download_files(paths)

    # --- mutating operations: denied (read-only) ---
    def write(self, file_path: str, content: str) -> WriteResult:
        """Deny file creation — the granted folder is read-only."""
        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """Deny file edits — the granted folder is read-only."""
        return EditResult(error=_READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Deny uploads — the granted folder is read-only."""
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


# Binary document formats the raw text read can't decode — routed through the ingestion parser
# so ``read_file`` returns their extracted text instead of crashing on a UTF-8 decode.
_DOC_EXTS = {".pdf", ".docx", ".xlsx"}

# Bounded, mtime-keyed cache so paginated reads of a large document don't re-parse it each call.
_doc_lines_cache: "OrderedDict[str, tuple[float, list[str]]]" = OrderedDict()
_DOC_CACHE_MAX = 8


def _flatten_document(host_path: str) -> list[str]:
    """Extract a parseable document into flat text lines, each section under its location header."""
    doc = extract_document(host_path)
    lines: list[str] = []
    for sec in doc.sections:
        if sec.text:
            lines.append(f"[{sec.location}]")
            lines.extend(sec.text.splitlines())
            lines.append("")
        elif sec.needs_ocr:
            lines.append(f"[{sec.location}] (sem texto extraível — provável página escaneada)")
            lines.append("")
    return lines


def _document_lines(host_path: str) -> list[str]:
    """Return the document's text lines, parsing on a cache miss or when the file changed."""
    try:
        mtime = os.path.getmtime(host_path)
    except OSError:
        mtime = 0.0
    cached = _doc_lines_cache.get(host_path)
    if cached and cached[0] == mtime:
        _doc_lines_cache.move_to_end(host_path)
        return cached[1]
    lines = _flatten_document(host_path)
    _doc_lines_cache[host_path] = (mtime, lines)
    _doc_lines_cache.move_to_end(host_path)
    while len(_doc_lines_cache) > _DOC_CACHE_MAX:
        _doc_lines_cache.popitem(last=False)
    return lines


class DocumentAwareBackend(BackendProtocol):
    """Wraps a folder backend so ``read`` on a PDF/Word/Excel returns extracted text.

    The deepagents ``FilesystemBackend`` reads files as UTF-8 text, which raises on a binary
    document — and the agent, seeing a tool error, retries until it hits the graph recursion
    limit. Here we intercept ``read`` for known document extensions and return parsed text
    (line-paginated via ``offset``/``limit``); every other operation delegates unchanged, so the
    wrapped backend's read-only enforcement and path-traversal safety are fully preserved.
    """

    def __init__(self, inner: BackendProtocol, root_dir: str) -> None:
        """Wrap ``inner``, resolving document reads against ``root_dir``."""
        self._inner = inner
        self._root_dir = os.path.abspath(root_dir)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read a file — extracting text for PDF/Word/Excel, delegating for everything else."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _DOC_EXTS:
            return self._inner.read(file_path, offset=offset, limit=limit)
        host_path = self._safe_host_path(file_path)
        if host_path is None:
            # Not resolvable inside the root — let the inner backend raise its normal not-found.
            return self._inner.read(file_path, offset=offset, limit=limit)
        try:
            lines = _document_lines(host_path)
        except Exception as e:  # noqa: BLE001 - return a clear, non-retryable message, never crash
            logger.exception("document_read_failed", file_path=file_path, error_type=type(e).__name__)
            return (
                f"Não foi possível extrair o texto de '{file_path}' ({type(e).__name__}). "
                "Se for um documento escaneado, use `buscar_documentos` para consultar o conteúdo."
            )
        if not lines:
            return (
                f"'{file_path}' não tem texto extraível (provável documento escaneado). "
                "Use `buscar_documentos` para consultar o conteúdo."
            )
        window = lines[offset : offset + limit] if limit else lines[offset:]
        body = "\n".join(window)
        remaining = len(lines) - (offset + len(window))
        if remaining > 0:
            body += (
                f"\n\n… (+{remaining} linhas — continue com offset={offset + len(window)}, ou use "
                "`grep`/`buscar_documentos` para ir direto ao trecho)"
            )
        return body

    def _safe_host_path(self, file_path: str) -> Optional[str]:
        """Resolve a virtual path to a host file confined under the root, or ``None``."""
        rel = file_path.lstrip("/\\")
        host = os.path.abspath(os.path.join(self._root_dir, rel))
        try:
            if os.path.commonpath([host, self._root_dir]) != self._root_dir:
                return None
        except ValueError:
            return None
        return host if os.path.isfile(host) else None

    # --- everything else delegates unchanged (read-only + traversal safety live in the wrapped backend) ---
    def ls_info(self, path: str) -> list[FileInfo]:
        """Delegate directory listing to the wrapped backend."""
        return self._inner.ls_info(path)

    def grep_raw(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None) -> list[GrepMatch] | str:
        """Delegate content search to the wrapped backend."""
        return self._inner.grep_raw(pattern, path=path, glob=glob)

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Delegate glob matching to the wrapped backend."""
        return self._inner.glob_info(pattern, path=path)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Delegate file download to the wrapped backend."""
        return self._inner.download_files(paths)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Delegate write to the wrapped backend (denied when read-only)."""
        return self._inner.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """Delegate edit to the wrapped backend (denied when read-only)."""
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Delegate upload to the wrapped backend (denied when read-only)."""
        return self._inner.upload_files(files)


def _workspace_prefix() -> str:
    """Return the virtual mount prefix (e.g. ``/workspace/``) the granted folder is exposed at."""
    return settings.SANDBOX_MOUNT_PATH.rstrip("/") + "/"


def build_folder_backend(root_dir: str, *, writable: bool = False) -> BackendProtocol:
    """Build the path-traversal-safe backend for one granted folder.

    ``virtual_mode=True`` is mandatory: it anchors all paths under ``root_dir`` and blocks
    traversal (``..``/``~``) and absolute escapes — so even a *writable* folder confines every
    write to ``root_dir`` and can never touch the host outside it. We never construct the backend
    without it. When ``writable`` is False (the secure default) the folder is additionally wrapped
    read-only so ``write``/``edit``/``upload`` are denied. Either way it is wrapped
    document-aware so ``read_file`` returns extracted text for PDF/Word/Excel.
    """
    fs = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
    base = fs if writable else ReadOnlyBackend(fs)
    return DocumentAwareBackend(base, root_dir)


def make_backend_factory(root_dir: str, *, writable: bool = False) -> BackendFactory:
    """Return a per-session backend factory for a Data Agent bound to ``root_dir``.

    The returned callable resolves the authorized root directory from the invocation's
    ``config["configurable"]`` (falling back to the ``root_dir`` captured for this session's
    agent), then returns a ``CompositeBackend`` routing ``/workspace/`` → the granted folder
    backend and every other path → ephemeral ``StateBackend`` scratch. ``writable`` (a per-agent
    capability, off by default) decides whether the folder allows writes; either way writes stay
    confined to ``root_dir`` via ``virtual_mode``. The factory does no setup I/O (no external
    resource is created) — a pure config read plus object construction, so first-response latency
    carries no ``docker run`` cost.
    """
    prefix = _workspace_prefix()

    def backend_factory(runtime: Any) -> BackendProtocol:
        resolved = _resolve_root_dir(runtime) or root_dir
        default = StateBackend(runtime)
        if not resolved:
            # No folder for this session — ephemeral state only (framework default behavior).
            return default
        return CompositeBackend(
            default=default,
            routes={prefix: build_folder_backend(resolved, writable=writable)},
        )

    return backend_factory


def _resolve_root_dir(runtime: Any) -> Optional[str]:
    """Read the session's authorized root dir from ``runtime.config['configurable']``.

    Defensive: middleware hooks may pass a runtime without ``config`` (e.g. the model-request
    runtime used only to decide tool visibility); in that case return ``None`` and let the caller
    fall back to the per-session value.
    """
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    return configurable.get(ROOT_DIR_CONFIG_KEY) or None
