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


def _workspace_prefix() -> str:
    """Return the virtual mount prefix (e.g. ``/workspace/``) the granted folder is exposed at."""
    return settings.SANDBOX_MOUNT_PATH.rstrip("/") + "/"


def build_folder_backend(root_dir: str, *, writable: bool = False) -> BackendProtocol:
    """Build the path-traversal-safe backend for one granted folder.

    ``virtual_mode=True`` is mandatory: it anchors all paths under ``root_dir`` and blocks
    traversal (``..``/``~``) and absolute escapes — so even a *writable* folder confines every
    write to ``root_dir`` and can never touch the host outside it. We never construct the backend
    without it. When ``writable`` is False (the secure default) the folder is additionally wrapped
    read-only so ``write``/``edit``/``upload`` are denied.
    """
    fs = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
    return fs if writable else ReadOnlyBackend(fs)


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
