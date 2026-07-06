"""Folder-grant path validation, shared by the per-session grant and per-agent binding.

Both entry points must apply the SAME security check before a host folder is ever mounted
into a sandbox: the folder must exist and resolve to a path under a configured
``SANDBOX_ALLOWED_ROOTS`` entry. This is re-checked on every use (bind time AND chat-time
materialization), so tightening the server allow-list immediately revokes stale bindings.
"""

import os

from fastapi import HTTPException

from src.app.core.common.config import settings


def is_within_allowed_roots(path: str, roots: list[str]) -> bool:
    """True if ``path`` is inside one of the configured allow-listed roots."""
    target = os.path.normcase(os.path.abspath(path))
    for root in roots:
        allowed_root = os.path.normcase(os.path.abspath(root))
        try:
            if os.path.commonpath([target, allowed_root]) == allowed_root:
                return True
        except ValueError:
            # different drives on Windows -> not comparable
            continue
    return False


def validate_grantable_folder(path: str) -> str:
    """Return the normalized absolute path of a grantable folder, or raise HTTPException.

    Raises:
        HTTPException: 503 if the sandbox is disabled, 400 if the path is not a directory,
            403 if grants are disabled or the path is outside the allowed roots.
    """
    if not settings.SANDBOX_ENABLED:
        raise HTTPException(status_code=503, detail="Sandbox desabilitado nesta instância.")

    abspath = os.path.abspath(path)
    if not os.path.isdir(abspath):
        raise HTTPException(status_code=400, detail="Pasta não encontrada ou não é um diretório.")

    if not settings.SANDBOX_ALLOWED_ROOTS:
        raise HTTPException(
            status_code=403,
            detail="Concessão de pastas desabilitada. Configure SANDBOX_ALLOWED_ROOTS no servidor.",
        )
    if not is_within_allowed_roots(abspath, settings.SANDBOX_ALLOWED_ROOTS):
        raise HTTPException(status_code=403, detail="Pasta fora das raízes permitidas.")

    return abspath
