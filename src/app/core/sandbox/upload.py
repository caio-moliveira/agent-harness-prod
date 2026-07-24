"""Persist a browser-uploaded folder into a server-managed directory (path-safe).

The destination directory is always derived from the authenticated user (never from client
input), so this never goes through ``SANDBOX_ALLOWED_ROOTS`` — that allow-list exists to gate
arbitrary host paths a client could name, which doesn't apply to a destination the server itself
picked. Every uploaded file's relative path DOES come from the client (its filename), so that is
what gets sanitized here — confined under the destination the same way ``sandbox/paths.py``
confines a granted folder.
"""

import os
import shutil

from fastapi import HTTPException, UploadFile

from src.app.core.common.config import settings
from src.app.core.common.logging import logger


def _safe_relative_path(dest_dir: str, filename: str) -> str:
    """Resolve an uploaded file's filename to a path confined under ``dest_dir``, or raise.

    Rejects any filename with a ``..`` segment or that otherwise resolves outside ``dest_dir``
    (e.g. an embedded absolute path) — the check that matters here, since ``filename`` is the one
    piece of this request that comes from the client.
    """
    rel = filename.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    # Reject ".." (parent traversal) and any segment containing ":" (a drive letter like "C:" is
    # not thrown away by os.path.join unless followed by a root separator — reject it outright
    # rather than rely on that nuance).
    if not parts or ".." in parts or any(":" in p for p in parts):
        raise HTTPException(status_code=400, detail=f"Nome de arquivo inválido: '{filename}'.")
    dest_abs = os.path.abspath(dest_dir)
    host_path = os.path.abspath(os.path.join(dest_abs, *parts))
    if os.path.commonpath([host_path, dest_abs]) != dest_abs:
        raise HTTPException(status_code=400, detail=f"Nome de arquivo inválido: '{filename}'.")
    return host_path


async def save_uploaded_folder(dest_dir: str, files: list[UploadFile]) -> str:
    """Replace ``dest_dir``'s content with the uploaded files, enforcing configured limits.

    Every file is read and validated before anything is written to disk — an oversized upload or
    a path-escaping filename is rejected without touching ``dest_dir``'s previous content. On
    success, ``dest_dir`` is wiped and rewritten from scratch, so re-uploading a folder replaces
    it cleanly (the incremental ingestion sync then diffs the new content on its own).
    """
    if not settings.SANDBOX_ENABLED:
        raise HTTPException(status_code=503, detail="Sandbox desabilitado nesta instância.")
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    if len(files) > settings.SANDBOX_UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Máximo de {settings.SANDBOX_UPLOAD_MAX_FILES} arquivos por envio (recebido {len(files)}).",
        )

    staged: list[tuple[str, bytes]] = []
    total_bytes = 0
    for f in files:
        host_path = _safe_relative_path(dest_dir, f.filename or "")
        content = await f.read()
        total_bytes += len(content)
        if total_bytes > settings.SANDBOX_UPLOAD_MAX_BYTES:
            max_mb = settings.SANDBOX_UPLOAD_MAX_BYTES // (1024 * 1024)
            raise HTTPException(status_code=413, detail=f"Envio excede o limite de {max_mb}MB.")
        staged.append((host_path, content))

    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    for host_path, content in staged:
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        with open(host_path, "wb") as out:
            out.write(content)

    logger.info("folder_upload_saved", dest_dir=dest_dir, file_count=len(staged), total_bytes=total_bytes)
    return dest_dir
