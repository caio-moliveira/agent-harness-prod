"""Confirmation gate for destructive edits to a writable granted folder (#57).

Wraps the versioned writable backend so editing an EXISTING file is parked for the user's
explicit confirmation instead of being applied inline — via the app's own outward-facing-action
confirmation service (the same one that already gates artifact export and plan approval), never
deepagents' native ``interrupt_on``, which this codebase doesn't use anywhere.

Only ``edit`` is gated, not ``write``: deepagents' real ``FilesystemBackend.write`` unconditionally
refuses to write to a path that already exists ("Cannot write to X because it already exists.
Read and then make an edit...") — ``write_file`` can only ever create a NEW file, never overwrite
one. ``edit_file`` is the only operation capable of modifying existing content, so it's the only
one worth gating; creating a new file is never gated (nothing to lose).

Only the ASYNC ``aedit`` path is gated — that is what the deep agent's real, async graph
invocation actually calls. The sync ``edit`` method applies immediately (as it did before this
wrapper existed): there is no running event loop to safely await ``hitl_service.request`` from
inside a plain sync call, and in practice the sync path is only ever exercised by direct/sync
callers (tests), never by the live agent.
"""

import os
from typing import Optional

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GrepMatch,
    WriteResult,
)

from src.app.init import hitl_service


class ConfirmationGateBackend(BackendProtocol):
    """Wraps a writable backend, parking any edit of an existing file for user confirmation."""

    def __init__(self, inner: BackendProtocol, root_dir: str, user_id: int, session_id: str) -> None:
        """Wrap ``inner``, gating edits to existing files under ``root_dir`` for ``(user_id, session_id)``."""
        self._inner = inner
        self._root_dir = os.path.abspath(root_dir)
        self._user_id = user_id
        self._session_id = session_id

    def _host_path(self, virtual_path: str) -> str:
        rel = virtual_path.lstrip("/\\")
        return os.path.abspath(os.path.join(self._root_dir, rel))

    def _exists(self, virtual_path: str) -> bool:
        return os.path.isfile(self._host_path(virtual_path))

    # --- async: the real path the live agent's async graph invocation calls ---
    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Delegate straight through — write can never overwrite (see module docstring)."""
        return await self._inner.awrite(file_path, content)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """Park an edit of an existing file for confirmation instead of applying it inline."""
        if not self._exists(file_path):
            # Editing a nonexistent file fails anyway — let that error surface immediately rather
            # than parking an edit that could never succeed.
            return await self._inner.aedit(file_path, old_string, new_string, replace_all=replace_all)

        action = await hitl_service.request(
            self._user_id,
            self._session_id,
            "file_mutation",
            {
                "operation": "edit",
                "root_dir": self._root_dir,
                "path": file_path,
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": replace_all,
            },
        )
        message = (
            f"pending_confirmation: a alteração em '{file_path}' está aguardando confirmação do "
            f"usuário (ação #{action.id} em 'Ações pendentes'). Não repita esta chamada — a mudança "
            "será aplicada quando o usuário confirmar, ou descartada se ele recusar."
        )
        return EditResult(error=message)

    # --- sync: applies immediately (see module docstring) ---
    def write(self, file_path: str, content: str) -> WriteResult:
        """Delegate to the wrapped backend (no gating on the sync path — see module docstring)."""
        return self._inner.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """Delegate to the wrapped backend (no gating on the sync path — see module docstring)."""
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    # --- everything else delegates unchanged ---
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
        """Delegate file download to the wrapped backend."""
        return self._inner.download_files(paths)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Delegate upload to the wrapped backend."""
        return self._inner.upload_files(files)
