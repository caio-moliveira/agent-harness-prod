"""Versioned wrapper for a writable sandbox backend (#55): every overwrite is snapshotted first.

Wraps a writable folder backend (``build_folder_backend(..., writable=True)``) so overwriting an
EXISTING file preserves its previous content as a recoverable version before the write/edit is
applied. A JSON manifest under ``.versions/`` tracks each file's version history, capped at a
configurable number of entries per path (oldest evicted first). Creating a brand-new file (no
prior content to lose) is not gated — only an actual overwrite is.

The manifest and snapshot blobs live under ``root_dir/.versions/`` (the same tree the wrapped
backend reads from), so ``ls_info``/``glob_info``/``grep_raw`` explicitly filter that directory
out — otherwise it would show up as regular folder content, and grep could match stale text
trapped in an old snapshot.

Deletion is out of scope here: deepagents' ``BackendProtocol`` has no delete/remove operation at
all today (the standard file tools are ls/read_file/write_file/edit_file/glob/grep), so there is
nothing existing to version-gate for removal.
"""

import json
import os
import time
import uuid
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

from src.app.core.common.config import settings

_VERSIONS_DIR = ".versions"
_MANIFEST_FILE = "manifest.json"


def _manifest_path(root_dir: str) -> str:
    return os.path.join(root_dir, _VERSIONS_DIR, _MANIFEST_FILE)


def _is_internal_path(path: str) -> bool:
    """True if ``path`` falls under the internal ``.versions/`` bookkeeping directory.

    The manifest and snapshot blobs live inside ``root_dir`` (there's nowhere else to put them
    that both backends agree on), so without this check they'd show up in the agent's own
    ``ls``/``glob``/``grep`` — including grep matching stale content inside old snapshots.
    """
    return _VERSIONS_DIR in path.replace("\\", "/").split("/")


def load_manifest(root_dir: str) -> list[dict]:
    """Return every version entry recorded for ``root_dir``, oldest first."""
    path = _manifest_path(root_dir)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(root_dir: str, entries: list[dict]) -> None:
    path = _manifest_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def versions_for(root_dir: str, rel_path: str) -> list[dict]:
    """Return ``rel_path``'s version history, oldest first."""
    return [e for e in load_manifest(root_dir) if e["path"] == rel_path]


def restore_latest_version(root_dir: str, rel_path: str) -> Optional[dict]:
    """Restore ``rel_path`` to its most recent saved version, removing that entry.

    Returns the restored entry, or ``None`` if there is no version to restore to.
    """
    entries = load_manifest(root_dir)
    same_path = [e for e in entries if e["path"] == rel_path]
    if not same_path:
        return None
    latest = max(same_path, key=lambda e: e["timestamp"])
    snapshot = os.path.join(root_dir, _VERSIONS_DIR, latest["snapshot_file"])
    host_path = os.path.abspath(os.path.join(root_dir, rel_path))
    with open(snapshot, "rb") as f:
        content = f.read()
    os.makedirs(os.path.dirname(host_path), exist_ok=True)
    with open(host_path, "wb") as f:
        f.write(content)
    os.remove(snapshot)
    _save_manifest(root_dir, [e for e in entries if e["id"] != latest["id"]])
    return latest


class VersioningBackend(BackendProtocol):
    """Wraps a writable backend so overwriting an existing file snapshots it first.

    ``root_dir`` is the same host directory the wrapped backend is rooted at — needed here to
    read pre-write file content (for the snapshot) and to persist the manifest, since neither is
    exposed by the wrapped backend's virtual-path-only methods.
    """

    def __init__(self, inner: BackendProtocol, root_dir: str, max_versions_per_file: Optional[int] = None) -> None:
        """Wrap ``inner``, snapshotting overwrites of files under ``root_dir``."""
        self._inner = inner
        self._root_dir = os.path.abspath(root_dir)
        self._max_versions = max_versions_per_file or settings.SANDBOX_MAX_VERSIONS_PER_FILE

    def _host_path(self, virtual_path: str) -> str:
        rel = virtual_path.lstrip("/\\")
        return os.path.abspath(os.path.join(self._root_dir, rel))

    def _snapshot_if_exists(self, virtual_path: str, operation: str) -> None:
        host = self._host_path(virtual_path)
        if not os.path.isfile(host):
            return  # brand-new file — nothing to lose, no snapshot needed
        with open(host, "rb") as f:
            content = f.read()

        rel_path = virtual_path.lstrip("/\\")
        snapshot_id = uuid.uuid4().hex
        snapshot_file = f"{snapshot_id}.snapshot"
        versions_dir = os.path.join(self._root_dir, _VERSIONS_DIR)
        os.makedirs(versions_dir, exist_ok=True)
        with open(os.path.join(versions_dir, snapshot_file), "wb") as f:
            f.write(content)

        entries = load_manifest(self._root_dir)
        entries.append(
            {
                "id": snapshot_id,
                "path": rel_path,
                "operation": operation,
                "timestamp": time.time(),
                "size": len(content),
                "snapshot_file": snapshot_file,
            }
        )
        same_path = sorted((e for e in entries if e["path"] == rel_path), key=lambda e: e["timestamp"])
        if len(same_path) > self._max_versions:
            to_evict = same_path[: len(same_path) - self._max_versions]
            evict_ids = {e["id"] for e in to_evict}
            for e in to_evict:
                snap = os.path.join(versions_dir, e["snapshot_file"])
                if os.path.isfile(snap):
                    os.remove(snap)
            entries = [e for e in entries if e["id"] not in evict_ids]
        _save_manifest(self._root_dir, entries)

    # --- writes: snapshot the previous version (if any) before delegating ---
    def write(self, file_path: str, content: str) -> WriteResult:
        """Snapshot any existing content at ``file_path``, then delegate the write."""
        self._snapshot_if_exists(file_path, "write")
        return self._inner.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """Snapshot the current content at ``file_path``, then delegate the edit."""
        self._snapshot_if_exists(file_path, "edit")
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    # --- everything else delegates unchanged, minus the internal .versions/ bookkeeping ---
    def ls_info(self, path: str) -> list[FileInfo]:
        """Delegate directory listing to the wrapped backend, hiding ``.versions/``."""
        return [f for f in self._inner.ls_info(path) if not _is_internal_path(f["path"])]

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Delegate file read to the wrapped backend."""
        return self._inner.read(file_path, offset=offset, limit=limit)

    def grep_raw(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None) -> list[GrepMatch] | str:
        """Delegate content search to the wrapped backend, excluding matches inside ``.versions/``.

        Without this, a grep could match text that only exists in a stale, already-overwritten
        snapshot — the agent would see it as if it were part of the folder's current content.
        """
        result = self._inner.grep_raw(pattern, path=path, glob=glob)
        if isinstance(result, str):
            return result
        return [m for m in result if not _is_internal_path(m["path"])]

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Delegate glob matching to the wrapped backend, hiding ``.versions/``."""
        return [f for f in self._inner.glob_info(pattern, path=path) if not _is_internal_path(f["path"])]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Delegate file download to the wrapped backend."""
        return self._inner.download_files(paths)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Delegate upload to the wrapped backend."""
        return self._inner.upload_files(files)
