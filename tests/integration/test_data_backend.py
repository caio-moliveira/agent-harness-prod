"""Tests for the Data Agent's per-session read-only FilesystemBackend (replaces Docker sandbox).

The security guarantees live in the backend factory (``src/app/core/sandbox/backend.py``): a
granted folder is exposed READ-ONLY under ``/workspace`` via a ``CompositeBackend`` routing to a
``FilesystemBackend(virtual_mode=True)``, isolated per session by resolving the root dir from the
invocation config. These tests exercise that layer directly (no LLM), covering:

  (a) successful reads inside the authorized root,
  (b) writes/edits denied (read-only),
  (c) path-escape attempts denied (``..`` / absolute escapes),
  (d) isolation between two concurrent sessions with different folders,

plus the invariants that the ``execute`` tool is never exposed and ``virtual_mode`` is enforced.
An HTTP-level test confirms ``/grant-folder`` no longer spins up any container.
"""

import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware, _supports_execution

from src.app.agents.data_agent.workspace_memory import WorkspaceMemoryMiddleware
from src.app.core.sandbox.backend import (
    ROOT_DIR_CONFIG_KEY,
    SKILLS_MOUNT,
    USER_SKILLS_MOUNT,
    DocumentAwareBackend,
    ReadOnlyBackend,
    build_folder_backend,
    make_backend_factory,
)
from src.app.core.sandbox.versioning import VersioningBackend


class FakeRuntime:
    """Minimal stand-in for a ToolRuntime: the factory reads ``config``; StateBackend reads ``state``."""

    def __init__(self, root_dir: str | None = None) -> None:
        configurable: dict = {"thread_id": "sess"}
        if root_dir is not None:
            configurable[ROOT_DIR_CONFIG_KEY] = root_dir
        self.config = {"configurable": configurable}
        self.state = {"files": {}}
        self.store = None
        self.tool_call_id = None


def _backend_for(root_dir: str, *, config_root: str | None = None):
    """Build the composite backend a session would use, resolving root from config."""
    factory = make_backend_factory(root_dir)
    return factory(FakeRuntime(root_dir=config_root if config_root is not None else root_dir))


# ---------------------------------------------------------------------------
# (a) reads inside the authorized root
# ---------------------------------------------------------------------------


class TestReadWithinRoot:
    def test_read_file_returns_content(self, tmp_path):
        (tmp_path / "vendas.csv").write_text("mes,receita\njan,1000\n", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        out = backend.read("/workspace/vendas.csv")
        assert "receita" in out
        assert "jan,1000" in out

    def test_ls_lists_workspace_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("A", encoding="utf-8")
        (tmp_path / "b.txt").write_text("B", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        paths = {fi["path"] for fi in backend.ls_info("/workspace")}
        assert "/workspace/a.txt" in paths
        assert "/workspace/b.txt" in paths

    def test_grep_finds_within_root(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello TODO world\n", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        matches = backend.grep_raw("TODO", path="/workspace")
        assert matches  # non-empty list of GrepMatch
        assert matches[0]["path"].startswith("/workspace/")

    def test_read_extracts_text_from_binary_document(self, tmp_path):
        # A binary Office/PDF doc would crash a raw UTF-8 read; the document-aware backend must
        # return extracted text instead (regression: PDF read looped to the recursion limit).
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["mes", "receita"])
        ws.append(["jan", 1000])
        wb.save(str(tmp_path / "vendas.xlsx"))

        backend = _backend_for(str(tmp_path))
        out = backend.read("/workspace/vendas.xlsx")
        assert "receita" in out
        assert "1000" in out

    def test_read_scanned_document_steers_to_page_image(self, tmp_path):
        # A document with no extractable text must not loop — it returns a clear, terminal hint.
        from openpyxl import Workbook

        Workbook().save(str(tmp_path / "empty.xlsx"))  # no rows => no extractable text
        backend = _backend_for(str(tmp_path))
        out = backend.read("/workspace/empty.xlsx")
        assert "read_page_image" in out


# ---------------------------------------------------------------------------
# (b) writes denied — read-only
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_write_denied_and_no_file_created(self, tmp_path):
        backend = _backend_for(str(tmp_path))

        res = backend.write("/workspace/new.txt", "x")
        assert res.error is not None
        assert "permission_denied" in res.error
        assert not (tmp_path / "new.txt").exists()

    def test_edit_denied_and_content_unchanged(self, tmp_path):
        target = tmp_path / "data.txt"
        target.write_text("original", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        res = backend.edit("/workspace/data.txt", "original", "hacked")
        assert res.error is not None
        assert target.read_text(encoding="utf-8") == "original"

    @pytest.mark.asyncio
    async def test_async_write_denied(self, tmp_path):
        backend = _backend_for(str(tmp_path))
        res = await backend.awrite("/workspace/new.txt", "x")
        assert res.error is not None
        assert not (tmp_path / "new.txt").exists()

    def test_upload_denied(self, tmp_path):
        backend = build_folder_backend(str(tmp_path))  # default: read-only, document-aware wrapper
        assert isinstance(backend, DocumentAwareBackend)
        assert isinstance(backend._inner, ReadOnlyBackend)  # read-only enforcement preserved
        responses = backend.upload_files([("/x.txt", b"data")])
        assert responses[0].error == "permission_denied"


# ---------------------------------------------------------------------------
# Writable folder (opt-in per agent) — writes allowed but still confined to root
# ---------------------------------------------------------------------------


class TestWritableFolder:
    def _backend(self, root_dir: str):
        factory = make_backend_factory(root_dir, writable=True)
        return factory(FakeRuntime(root_dir=root_dir))

    def test_write_creates_file_inside_root(self, tmp_path):
        backend = self._backend(str(tmp_path))
        res = backend.write("/workspace/report.md", "# Metas\n")
        assert res.error is None
        created = tmp_path / "report.md"
        assert created.exists()
        assert created.read_text(encoding="utf-8") == "# Metas\n"

    def test_edit_updates_file(self, tmp_path):
        (tmp_path / "d.txt").write_text("old", encoding="utf-8")
        backend = self._backend(str(tmp_path))
        res = backend.edit("/workspace/d.txt", "old", "new")
        assert res.error is None
        assert (tmp_path / "d.txt").read_text(encoding="utf-8") == "new"

    def test_write_still_confined_by_virtual_mode(self, tmp_path):
        # Even writable, a traversal write must not escape the granted root.
        backend = self._backend(str(tmp_path))
        with pytest.raises(ValueError):
            backend.write("/workspace/../escape.md", "pwned")
        assert not (tmp_path.parent / "escape.md").exists()


# ---------------------------------------------------------------------------
# (c) path-escape attempts denied
# ---------------------------------------------------------------------------


class TestPathEscape:
    def test_dotdot_traversal_blocked(self, tmp_path):
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("TOP SECRET", encoding="utf-8")
        (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        with pytest.raises(ValueError):
            backend.read("/workspace/../secret.txt")

    def test_absolute_escape_neutralized(self, tmp_path):
        # An absolute-looking path under /workspace is anchored to the root (virtual_mode),
        # so it can never reach a real host file outside the granted folder.
        secret = tmp_path.parent / "etc_passwd"
        secret.write_text("root:x:0:0", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        out = backend.read("/workspace//etc_passwd")
        assert "root:x:0:0" not in out
        assert "not found" in out.lower() or "error" in out.lower()

    def test_non_workspace_path_hits_ephemeral_not_host(self, tmp_path):
        # Any path outside /workspace routes to the ephemeral StateBackend default, never disk.
        (tmp_path / "real.txt").write_text("on disk", encoding="utf-8")
        backend = _backend_for(str(tmp_path))

        out = backend.read(f"{tmp_path}/real.txt".replace("\\", "/"))
        assert "on disk" not in out


# ---------------------------------------------------------------------------
# (d) two concurrent sessions with different folders are isolated
# ---------------------------------------------------------------------------


class TestCrossSessionIsolation:
    def test_config_root_wins_and_folders_are_isolated(self, tmp_path):
        folder_a = tmp_path / "A"
        folder_b = tmp_path / "B"
        folder_a.mkdir()
        folder_b.mkdir()
        (folder_a / "a.txt").write_text("AAA", encoding="utf-8")
        (folder_b / "b.txt").write_text("BBB", encoding="utf-8")

        # A single factory resolves the root per invocation from the runtime config: the same
        # factory serves session A and session B with different folders.
        factory = make_backend_factory(str(folder_a))
        backend_a = factory(FakeRuntime(root_dir=str(folder_a)))
        backend_b = factory(FakeRuntime(root_dir=str(folder_b)))

        assert "AAA" in backend_a.read("/workspace/a.txt")
        assert "BBB" in backend_b.read("/workspace/b.txt")

        # Neither session can read the other's file (different roots, virtual paths).
        assert "AAA" not in backend_b.read("/workspace/a.txt")
        assert "BBB" not in backend_a.read("/workspace/b.txt")

    def test_missing_config_falls_back_to_session_root(self, tmp_path):
        (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
        factory = make_backend_factory(str(tmp_path))
        backend = factory(FakeRuntime(root_dir=None))  # no override in config
        assert "AAA" in backend.read("/workspace/a.txt")


# ---------------------------------------------------------------------------
# Skills mounts: bundled (/skills/) vs. the caller's materialized, approved
# library (/skills/user/) — regression coverage for a skill attached via the
# approval workflow silently never reaching the agent (#materialize routing).
# ---------------------------------------------------------------------------


class TestSkillsMounts:
    def _skill_dir(self, base: os.PathLike | str, slug: str, name: str) -> None:
        skill_path = os.path.join(str(base), slug)
        os.makedirs(skill_path, exist_ok=True)
        with open(os.path.join(skill_path, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(f"---\nname: {name}\ndescription: test skill\n---\n\nBody.\n")

    def test_bundled_skill_readable_at_skills_mount(self, tmp_path):
        bundled = tmp_path / "bundled"
        self._skill_dir(bundled, "relatorios", "relatorios")
        factory = make_backend_factory(str(tmp_path / "root"), skills_dir=str(bundled))
        backend = factory(FakeRuntime(root_dir=str(tmp_path / "root")))
        assert "relatorios" in backend.read(f"{SKILLS_MOUNT}relatorios/SKILL.md")

    def test_user_skill_readable_at_user_skills_mount(self, tmp_path):
        """A skill materialized from the approval workflow must resolve through the backend.

        Passing that directory's raw host path directly into ``create_deep_agent(skills=[...])``
        (instead of mounting it here) silently no-ops: SkillsMiddleware has no direct filesystem
        access, so an unrouted path falls through to the ephemeral StateBackend, which finds
        nothing. This is the regression a user hit ("criar-material" skill never usable).
        """
        materialized = tmp_path / "materialized" / "agent_1"
        self._skill_dir(materialized, "criar-material", "criar-material")
        factory = make_backend_factory(str(tmp_path / "root"), user_skills_dir=str(materialized))
        backend = factory(FakeRuntime(root_dir=str(tmp_path / "root")))
        assert "criar-material" in backend.read(f"{USER_SKILLS_MOUNT}criar-material/SKILL.md")

    def test_bundled_and_user_skills_coexist_without_collision(self, tmp_path):
        bundled = tmp_path / "bundled"
        materialized = tmp_path / "materialized" / "agent_1"
        self._skill_dir(bundled, "relatorios", "relatorios")
        self._skill_dir(materialized, "criar-material", "criar-material")
        factory = make_backend_factory(
            str(tmp_path / "root"), skills_dir=str(bundled), user_skills_dir=str(materialized)
        )
        backend = factory(FakeRuntime(root_dir=str(tmp_path / "root")))
        assert "relatorios" in backend.read(f"{SKILLS_MOUNT}relatorios/SKILL.md")
        assert "criar-material" in backend.read(f"{USER_SKILLS_MOUNT}criar-material/SKILL.md")
        # Wrong side of the mount never sees the other's skill.
        assert "error" in backend.read(f"{SKILLS_MOUNT}criar-material/SKILL.md").lower()

    def test_raw_host_path_in_skills_list_would_silently_fail(self, tmp_path):
        """Documents *why* ``USER_SKILLS_MOUNT`` exists.

        An unrouted absolute path resolves to the ephemeral StateBackend default, not the real
        directory on disk — the exact bug this mount fixes.
        """
        materialized = tmp_path / "materialized" / "agent_1"
        self._skill_dir(materialized, "criar-material", "criar-material")
        factory = make_backend_factory(str(tmp_path / "root"))  # no user_skills_dir mounted
        backend = factory(FakeRuntime(root_dir=str(tmp_path / "root")))
        raw_path = str(materialized).replace("\\", "/")
        result = backend.read(f"{raw_path}/criar-material/SKILL.md")
        assert result.startswith("Error:")  # falls through to StateBackend, which never heard of it
        assert "criar-material: test skill" not in result


# ---------------------------------------------------------------------------
# Workspace AGENTS.md memory: read through the existing /workspace/ route, no
# new mount needed (RF-24/25/26) — missing file must degrade gracefully.
# ---------------------------------------------------------------------------


class TestWorkspaceMemory:
    def test_agents_md_loads_through_existing_workspace_route(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Resumo da pasta: vendas por região.", encoding="utf-8")
        backend = _backend_for(str(tmp_path))
        mw = WorkspaceMemoryMiddleware(backend=None, sources=["/workspace/AGENTS.md"])
        content = mw._load_memory_from_backend_sync(backend, "/workspace/AGENTS.md")
        assert content is not None
        assert "vendas por região" in content

    def test_missing_agents_md_is_skipped_gracefully(self, tmp_path):
        backend = _backend_for(str(tmp_path))  # no AGENTS.md written in this folder
        mw = WorkspaceMemoryMiddleware(backend=None, sources=["/workspace/AGENTS.md"])
        content = mw._load_memory_from_backend_sync(backend, "/workspace/AGENTS.md")
        assert content is None

    def test_formatted_memory_excludes_self_editing_guidance(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Use SQL para tudo.", encoding="utf-8")
        backend = _backend_for(str(tmp_path))
        mw = WorkspaceMemoryMiddleware(backend=None, sources=["/workspace/AGENTS.md"])
        content = mw._load_memory_from_backend_sync(backend, "/workspace/AGENTS.md")
        formatted = mw._format_agent_memory({"/workspace/AGENTS.md": content})
        assert "Use SQL para tudo." in formatted
        assert "edit_file" not in formatted
        assert "memory_guidelines" not in formatted


# ---------------------------------------------------------------------------
# Invariants: execute never exposed, virtual_mode enforced
# ---------------------------------------------------------------------------


class TestBackendInvariants:
    def test_backend_is_not_a_sandbox(self, tmp_path):
        backend = _backend_for(str(tmp_path))
        assert isinstance(backend, CompositeBackend)
        assert not isinstance(backend, SandboxBackendProtocol)
        assert _supports_execution(backend) is False  # => FilesystemMiddleware drops `execute`

    def test_virtual_mode_is_enforced(self, tmp_path):
        backend = build_folder_backend(str(tmp_path))  # document-aware → read-only → FS backend
        assert isinstance(backend, DocumentAwareBackend)
        readonly = backend._inner
        assert isinstance(readonly, ReadOnlyBackend)
        # The innermost FilesystemBackend must run in virtual mode (traversal guard).
        assert readonly._inner.virtual_mode is True

    def test_writable_backend_wraps_bare_filesystem_in_virtual_mode(self, tmp_path):
        writable = build_folder_backend(str(tmp_path), writable=True)
        # Writable => document-aware wrapper over a VersioningBackend (not read-only wrapped).
        assert isinstance(writable, DocumentAwareBackend)
        versioning = writable._inner
        assert isinstance(versioning, VersioningBackend)
        fs = versioning._inner
        assert isinstance(fs, FilesystemBackend)
        assert fs.virtual_mode is True


# ---------------------------------------------------------------------------
# Integration: the real FilesystemMiddleware resolves our factory per tool call
# ---------------------------------------------------------------------------


class TestFilesystemMiddlewareWiring:
    """Drive the actual deepagents file tools (not the backend in isolation) through our factory."""

    def _tools(self, root_dir: str, *, writable: bool = False) -> dict:
        mw = FilesystemMiddleware(backend=make_backend_factory(root_dir, writable=writable))
        return {t.name: t for t in mw.tools}

    def test_read_tool_serves_granted_file(self, tmp_path):
        (tmp_path / "report.txt").write_text("quarterly numbers", encoding="utf-8")
        tools = self._tools(str(tmp_path))
        out = tools["read_file"].func(file_path="/workspace/report.txt", runtime=FakeRuntime(root_dir=str(tmp_path)))
        assert "quarterly numbers" in out

    def test_write_tool_is_denied_by_default(self, tmp_path):
        tools = self._tools(str(tmp_path))
        out = tools["write_file"].func(
            file_path="/workspace/new.txt", content="x", runtime=FakeRuntime(root_dir=str(tmp_path))
        )
        assert "permission_denied" in str(out)
        assert not (tmp_path / "new.txt").exists()

    def test_write_tool_succeeds_when_writable(self, tmp_path):
        tools = self._tools(str(tmp_path), writable=True)
        out = tools["write_file"].func(
            file_path="/workspace/metas_2026.md", content="# Metas\n", runtime=FakeRuntime(root_dir=str(tmp_path))
        )
        assert "permission_denied" not in str(out)
        created = tmp_path / "metas_2026.md"
        assert created.exists()
        assert created.read_text(encoding="utf-8") == "# Metas\n"

    def test_execute_tool_reports_unavailable(self, tmp_path):
        tools = self._tools(str(tmp_path))
        out = tools["execute"].func(command="ls", runtime=FakeRuntime(root_dir=str(tmp_path)))
        assert "not available" in out.lower()


# ---------------------------------------------------------------------------
# HTTP: /grant-folder needs no container
# ---------------------------------------------------------------------------


async def _register_and_session_token(client: AsyncClient) -> str:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "folder-user@example.com", "password": "TestPass123!"},
    )
    assert reg.status_code == 200
    user_token = reg.json()["token"]["access_token"]
    sess = await client.post("/api/v1/auth/session", headers={"Authorization": f"Bearer {user_token}"})
    assert sess.status_code == 200
    return sess.json()["token"]["access_token"]


class TestGrantFolderEndpoint:
    @pytest.mark.asyncio
    async def test_grant_folder_sets_source_without_container(self, client: AsyncClient, tmp_path):
        from src.app.core.common import config as config_module

        token = await _register_and_session_token(client)
        data = tmp_path / "data"
        data.mkdir()
        (data / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        headers = {"Authorization": f"Bearer {token}"}
        with (
            patch.object(config_module.settings, "SANDBOX_ENABLED", True),
            patch.object(config_module.settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)]),
        ):
            granted = await client.post("/api/v1/data-agent/grant-folder", json={"path": str(data)}, headers=headers)
            assert granted.status_code == 200, granted.text
            assert granted.json()["granted"] is True

            status = await client.get("/api/v1/data-agent/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["folder"] is not None

            gone = await client.post("/api/v1/data-agent/disconnect", headers=headers)
            assert gone.status_code == 200


class TestUploadFolderEndpoint:
    @pytest.mark.asyncio
    async def test_upload_folder_sets_source(self, client: AsyncClient, tmp_path):
        from src.app.core.common import config as config_module

        token = await _register_and_session_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        files = [
            ("files", ("dados.csv", b"a,b\n1,2\n", "text/csv")),
            ("files", ("sub/nota.txt", b"hello", "text/plain")),
        ]
        with patch.object(config_module.settings, "SANDBOX_UPLOAD_ROOT", str(tmp_path / "uploads")):
            uploaded = await client.post("/api/v1/data-agent/upload-folder", files=files, headers=headers)
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["granted"] is True

            status = await client.get("/api/v1/data-agent/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["folder"] is not None

            gone = await client.post("/api/v1/data-agent/disconnect", headers=headers)
            assert gone.status_code == 200


async def _register_and_session(client: AsyncClient, email: str) -> tuple[str, str]:
    """Register a user and open a session, returning (session_id, session_token)."""
    reg = await client.post("/api/v1/auth/register", json={"email": email, "password": "TestPass123!"})
    assert reg.status_code == 200
    user_token = reg.json()["token"]["access_token"]
    sess = await client.post("/api/v1/auth/session", headers={"Authorization": f"Bearer {user_token}"})
    assert sess.status_code == 200
    body = sess.json()
    return body["session_id"], body["token"]["access_token"]


class TestSessionFilesEndpoint:
    """GET /{session_id}/files — backs the composer's `@` mention picker (#64)."""

    @pytest.mark.asyncio
    async def test_lists_granted_folder_shallow(self, client: AsyncClient, tmp_path):
        from src.app.core.common import config as config_module

        session_id, token = await _register_and_session(client, "files-list-user@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        data = tmp_path / "data"
        data.mkdir()
        (data / "vendas.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (data / "sub").mkdir()
        (data / "sub" / "nested.txt").write_text("hidden one level down", encoding="utf-8")

        with (
            patch.object(config_module.settings, "SANDBOX_ENABLED", True),
            patch.object(config_module.settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)]),
        ):
            granted = await client.post("/api/v1/data-agent/grant-folder", json={"path": str(data)}, headers=headers)
            assert granted.status_code == 200, granted.text

            resp = await client.get(f"/api/v1/data-agent/{session_id}/files", headers=headers)
            assert resp.status_code == 200
            paths = {f["path"] for f in resp.json()["files"]}
            assert "/vendas.csv" in paths
            # shallow listing: the file nested one level down is not itself surfaced...
            assert not any("nested.txt" in p for p in paths)
            # ...only its parent directory is, as a dir entry
            assert any(p.rstrip("/") == "/sub" for p in paths)

    @pytest.mark.asyncio
    async def test_empty_when_no_folder_granted(self, client: AsyncClient):
        session_id, token = await _register_and_session(client, "files-list-empty@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/api/v1/data-agent/{session_id}/files", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["files"] == []

    @pytest.mark.asyncio
    async def test_requires_session_ownership(self, client: AsyncClient):
        session_id, _owner_token = await _register_and_session(client, "files-list-owner@example.com")
        _, attacker_token = await _register_and_session(client, "files-list-attacker@example.com")

        resp = await client.get(
            f"/api/v1/data-agent/{session_id}/files",
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert resp.status_code == 403


class TestFileDownloadResolution:
    """The files/download path resolver confines to the granted folder and rejects traversal."""

    def test_confines_and_rejects_traversal(self, tmp_path, monkeypatch):
        import os

        from src.app.api.v1.data_agent import _resolve_in_folder
        from src.app.core.common.config import settings

        (tmp_path / "plano.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)])

        ok = _resolve_in_folder(str(tmp_path), "/workspace/plano.md")
        assert ok == os.path.normpath(str(tmp_path / "plano.md"))

        assert _resolve_in_folder(str(tmp_path), "../secret.txt") is None  # traversal
        assert _resolve_in_folder(str(tmp_path), "/workspace/../../etc/passwd") is None
