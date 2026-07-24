"""Tests for versioned writes on a writable granted folder (#55): snapshot + undo + eviction.

Exercises ``VersioningBackend`` and the version-history tools directly against a temp directory —
no HTTP, no LLM — mirroring the style of ``test_data_backend.py``'s direct backend tests.

``FakeFilesystemBackend.write`` mirrors deepagents' real ``FilesystemBackend.write``, which
unconditionally refuses to write to a path that already exists — only ``edit`` modifies existing
content. Getting this wrong here would validate ``VersioningBackend`` against behavior the real
backend doesn't have (a bug caught for real while wiring up #57 — a "write" that returns an
error must never be versioned as if it had succeeded).

Deletion is out of scope: deepagents' ``BackendProtocol`` has no delete/remove operation at all,
so there is nothing existing to version-gate for removal (see ``versioning.py``'s module docstring).
"""

import os

from src.app.core.sandbox.versioning import VersioningBackend, restore_latest_version, versions_for
from src.app.agents.data_agent.version_tools import make_version_tools


class FakeFilesystemBackend:
    """A minimal stand-in for deepagents' FilesystemBackend: writes/edits real files under root_dir."""

    def __init__(self, root_dir: str) -> None:
        self._root_dir = root_dir

    def _host(self, virtual_path: str) -> str:
        return os.path.join(self._root_dir, virtual_path.lstrip("/"))

    def write(self, file_path: str, content: str):
        from deepagents.backends.protocol import WriteResult

        host = self._host(file_path)
        if os.path.isfile(host):
            # Matches the real FilesystemBackend: write only ever creates a new file.
            return WriteResult(error=f"Cannot write to {file_path} because it already exists.")
        with open(host, "w", encoding="utf-8") as f:
            f.write(content)
        return WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        from deepagents.backends.protocol import EditResult

        host = self._host(file_path)
        with open(host, "r", encoding="utf-8") as f:
            current = f.read()
        updated = current.replace(old_string, new_string, -1 if replace_all else 1)
        with open(host, "w", encoding="utf-8") as f:
            f.write(updated)
        return EditResult(path=file_path)


class TestVersioningBackend:
    def test_first_write_is_not_versioned(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/novo.txt", "conteúdo inicial")

        assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "conteúdo inicial"
        assert versions_for(str(tmp_path), "novo.txt") == []

    def test_write_on_existing_path_fails_and_is_not_versioned(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/relatorio.md", "versão 1")

        result = backend.write("/relatorio.md", "versão 2")

        assert result.error is not None
        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "versão 1"  # unchanged
        assert versions_for(str(tmp_path), "relatorio.md") == []  # nothing to version — it failed

    def test_edit_versions_the_prior_content(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/nota.txt", "original")
        backend.edit("/nota.txt", "original", "editado")

        assert (tmp_path / "nota.txt").read_text(encoding="utf-8") == "editado"
        entries = versions_for(str(tmp_path), "nota.txt")
        assert len(entries) == 1
        assert entries[0]["operation"] == "edit"

    def test_undo_restores_exact_prior_bytes(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/dados.txt", "linha original\ncom acentuação")
        backend.edit("/dados.txt", "linha original\ncom acentuação", "linha nova")

        restored = restore_latest_version(str(tmp_path), "dados.txt")

        assert restored is not None
        assert (tmp_path / "dados.txt").read_text(encoding="utf-8") == "linha original\ncom acentuação"
        # The restored version is consumed — no longer listed.
        assert versions_for(str(tmp_path), "dados.txt") == []

    def test_undo_with_no_version_returns_none(self, tmp_path):
        assert restore_latest_version(str(tmp_path), "nunca-existiu.txt") is None

    def test_eviction_drops_the_oldest_version_first(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path), max_versions_per_file=2)
        backend.write("/log.txt", "v1")
        backend.edit("/log.txt", "v1", "v2")  # snapshots v1
        backend.edit("/log.txt", "v2", "v3")  # snapshots v2
        backend.edit("/log.txt", "v3", "v4")  # snapshots v3 -> now 3 versions, cap is 2 -> evict v1's snapshot

        entries = versions_for(str(tmp_path), "log.txt")
        assert len(entries) == 2
        # The oldest surviving version's snapshot must be the one holding "v2" (v1's snapshot evicted).
        contents = set()
        for e in entries:
            with open(tmp_path / ".versions" / e["snapshot_file"], "r", encoding="utf-8") as f:
                contents.add(f.read())
        assert contents == {"v2", "v3"}


class TestVersionTools:
    def test_no_tools_without_a_writable_folder(self, tmp_path):
        assert make_version_tools(str(tmp_path), False) == []
        assert make_version_tools(None, True) == []

    def test_listar_versoes_reports_no_history_for_untouched_file(self, tmp_path):
        (listar, _undo) = make_version_tools(str(tmp_path), True)
        out = listar.func(caminho="nunca-tocado.txt")
        assert "Nenhuma versão salva" in out

    def test_listar_and_desfazer_round_trip(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/plano.md", "rascunho 1")
        backend.edit("/plano.md", "rascunho 1", "rascunho 2")

        (listar, desfazer) = make_version_tools(str(tmp_path), True)
        listing = listar.func(caminho="/workspace/plano.md")  # mount-prefixed path also works
        assert "edit" in listing

        result = desfazer.func(caminho="plano.md")
        assert "restaurado" in result
        assert (tmp_path / "plano.md").read_text(encoding="utf-8") == "rascunho 1"
