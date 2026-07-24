"""Tests for versioned writes on a writable granted folder (#55): snapshot + undo + eviction.

Exercises ``VersioningBackend`` and the version-history tools directly against a temp directory —
no HTTP, no LLM — mirroring the style of ``test_data_backend.py``'s direct backend tests.

Deletion is out of scope: deepagents' ``BackendProtocol`` has no delete/remove operation at all,
so there is nothing existing to version-gate for removal (see ``versioning.py``'s module docstring).
"""

from src.app.core.sandbox.versioning import VersioningBackend, restore_latest_version, versions_for
from src.app.agents.data_agent.version_tools import make_version_tools


class FakeFilesystemBackend:
    """A minimal stand-in for deepagents' FilesystemBackend: writes/edits real files under root_dir."""

    def __init__(self, root_dir: str) -> None:
        self._root_dir = root_dir

    def _host(self, virtual_path: str) -> str:
        import os

        return os.path.join(self._root_dir, virtual_path.lstrip("/"))

    def write(self, file_path: str, content: str):
        from deepagents.backends.protocol import WriteResult

        with open(self._host(file_path), "w", encoding="utf-8") as f:
            f.write(content)
        return WriteResult(error=None)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        from deepagents.backends.protocol import EditResult

        host = self._host(file_path)
        with open(host, "r", encoding="utf-8") as f:
            current = f.read()
        updated = current.replace(old_string, new_string, -1 if replace_all else 1)
        with open(host, "w", encoding="utf-8") as f:
            f.write(updated)
        return EditResult(error=None)

    def ls_info(self, path: str):
        """Unfiltered listing (mirrors real FilesystemBackend: no dotfile exclusion)."""
        import os

        base = self._host(path)
        return [{"path": "/" + name, "is_dir": os.path.isdir(os.path.join(base, name))} for name in os.listdir(base)]

    def glob_info(self, pattern: str, path: str = "/"):
        """Unfiltered recursive glob (mirrors real FilesystemBackend: no dotfile exclusion)."""
        from pathlib import Path

        root = Path(self._host(path))
        return [{"path": "/" + str(p.relative_to(root)).replace("\\", "/")} for p in root.rglob(pattern) if p.is_file()]

    def grep_raw(self, pattern: str, path=None, glob=None):
        """Unfiltered recursive text search (mirrors real FilesystemBackend: no dotfile exclusion)."""
        from pathlib import Path

        root = Path(self._host(path or "/"))
        matches = []
        for fp in root.rglob("*"):
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    matches.append({"path": "/" + str(fp.relative_to(root)).replace("\\", "/"), "line": i, "text": line})
        return matches


class TestVersioningBackend:
    def test_first_write_is_not_versioned(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/novo.txt", "conteúdo inicial")

        assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "conteúdo inicial"
        assert versions_for(str(tmp_path), "novo.txt") == []

    def test_overwrite_preserves_previous_content_as_a_version(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/relatorio.md", "versão 1")
        backend.write("/relatorio.md", "versão 2")

        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "versão 2"
        entries = versions_for(str(tmp_path), "relatorio.md")
        assert len(entries) == 1
        assert entries[0]["operation"] == "write"

    def test_edit_also_versions_the_prior_content(self, tmp_path):
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
        backend.write("/dados.txt", "linha nova")

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
        backend.write("/log.txt", "v2")  # snapshots v1
        backend.write("/log.txt", "v3")  # snapshots v2
        backend.write("/log.txt", "v4")  # snapshots v3 -> now 3 versions, cap is 2 -> evict v1's snapshot

        entries = versions_for(str(tmp_path), "log.txt")
        assert len(entries) == 2
        # The oldest surviving version's snapshot must be the one holding "v2" (v1's snapshot evicted).
        contents = set()
        for e in entries:
            with open(tmp_path / ".versions" / e["snapshot_file"], "r", encoding="utf-8") as f:
                contents.add(f.read())
        assert contents == {"v2", "v3"}

    def test_versions_dir_hidden_from_ls_glob_and_grep(self, tmp_path):
        backend = VersioningBackend(FakeFilesystemBackend(str(tmp_path)), str(tmp_path))
        backend.write("/relatorio.md", "rascunho com um segredo antigo")
        backend.write("/relatorio.md", "versão final, sem o segredo")

        # Sanity check: the wrapped (fake) backend itself does NOT filter — proves the assertions
        # below exercise VersioningBackend's own filtering, not an accident of the fake.
        raw_ls = backend._inner.ls_info("/")
        assert any(f["path"] == "/.versions" for f in raw_ls)

        ls = backend.ls_info("/")
        assert all(f["path"] != "/.versions" for f in ls)
        assert any(f["path"] == "/relatorio.md" for f in ls)

        globbed = backend.glob_info("**/*")
        assert all(".versions" not in f["path"] for f in globbed)

        # The old snapshot still contains "segredo antigo" on disk — grep must not surface it.
        hits = backend.grep_raw("segredo antigo")
        assert hits == []
        assert backend.grep_raw("sem o segredo")[0]["path"] == "/relatorio.md"


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
        backend.write("/plano.md", "rascunho 2")

        (listar, desfazer) = make_version_tools(str(tmp_path), True)
        listing = listar.func(caminho="/workspace/plano.md")  # mount-prefixed path also works
        assert "write" in listing

        result = desfazer.func(caminho="plano.md")
        assert "restaurado" in result
        assert (tmp_path / "plano.md").read_text(encoding="utf-8") == "rascunho 1"
