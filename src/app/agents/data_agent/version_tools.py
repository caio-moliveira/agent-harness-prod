"""Version-history tools for a writable granted folder (#55): list and undo.

Bound to one session's granted folder. Only meaningful when the folder is writable — a read-only
folder never accumulates versions, since nothing ever overwrites a file in it.
"""

from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.app.core.common.config import settings
from src.app.core.sandbox.versioning import restore_latest_version, versions_for


def _strip_mount_prefix(path: str) -> str:
    """Normalize an agent-supplied path (with or without the /workspace/ mount prefix) to relative."""
    prefix = settings.SANDBOX_MOUNT_PATH.rstrip("/") + "/"
    if path.startswith(prefix):
        path = path[len(prefix) :]
    return path.lstrip("/")


def make_version_tools(root_dir: Optional[str], writable_folder: bool) -> list[BaseTool]:
    """Build the version-history tools bound to one session's writable folder.

    Empty list when there is no folder, or it isn't writable — nothing to list/undo.
    """
    if not root_dir or not writable_folder:
        return []

    @tool
    def listar_versoes(caminho: str) -> str:
        """Lista o histórico de versões salvas de um arquivo da pasta (mais recente primeiro).

        Use antes de desfazer uma mudança, ou quando o usuário perguntar o que mudou num arquivo.
        ``caminho`` é relativo à pasta (ex.: "relatorio.md"), com ou sem o prefixo "/workspace/".
        """
        rel = _strip_mount_prefix(caminho)
        entries = versions_for(root_dir, rel)
        if not entries:
            return f"Nenhuma versão salva para '{caminho}' — ele nunca foi sobrescrito nesta pasta."
        entries = sorted(entries, key=lambda e: e["timestamp"], reverse=True)
        lines = [f"Versões de '{caminho}' (mais recente primeiro):"]
        for e in entries:
            lines.append(f"- {e['operation']} em {e['timestamp']:.0f} ({e['size']} bytes)")
        return "\n".join(lines)

    @tool
    def desfazer_ultima_alteracao(caminho: str) -> str:
        """Desfaz a alteração mais recente de um arquivo, restaurando seu conteúdo anterior.

        ``caminho`` é relativo à pasta (ex.: "relatorio.md"), com ou sem o prefixo "/workspace/".
        Só funciona se houver uma versão anterior salva (isto é, o arquivo já foi sobrescrito
        alguma vez) — a primeira criação de um arquivo não gera versão para desfazer.
        """
        rel = _strip_mount_prefix(caminho)
        restored = restore_latest_version(root_dir, rel)
        if restored is None:
            return f"Nada para desfazer em '{caminho}' — nenhuma versão anterior salva."
        return f"'{caminho}' restaurado à versão anterior à sua última {restored['operation']}."

    return [listar_versoes, desfazer_ultima_alteracao]
