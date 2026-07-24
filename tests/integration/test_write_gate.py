"""Tests for the confirmation gate on destructive folder edits (#57).

Editing an existing file in a writable granted folder must park for the user's explicit
confirmation instead of applying inline — via the app's own outward-facing-action confirmation
service (the same one used for artifact export and plan approval), never deepagents' native
``interrupt_on``, which this codebase doesn't use anywhere.

Only ``edit`` is gated. ``write`` never overwrites at all — deepagents' real
``FilesystemBackend.write`` unconditionally refuses when the path already exists, so there is no
destructive write path to gate (confirmed by testing; see write_gate.py's module docstring).
Deletion is out of scope for the same reason as #55: no delete/remove operation exists in
deepagents' ``BackendProtocol`` at all.
"""

import pytest
from httpx import AsyncClient

from src.app.agents.data_agent.write_gate import ConfirmationGateBackend
from src.app.core.sandbox.backend import build_folder_backend
from src.app.core.sandbox.versioning import versions_for

pytestmark = pytest.mark.asyncio


def _gate(root_dir: str, user_id: int = 1, session_id: str = "s1") -> ConfirmationGateBackend:
    inner = build_folder_backend(root_dir, writable=True)
    return ConfirmationGateBackend(inner, root_dir, user_id, session_id)


class TestConfirmationGateBackend:
    async def test_new_file_via_write_is_never_gated(self, client: AsyncClient, tmp_path):
        gate = _gate(str(tmp_path))

        result = await gate.awrite("/novo.txt", "conteúdo")

        assert result.error is None
        assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "conteúdo"

    async def test_write_on_existing_path_is_not_gated_and_fails_naturally(self, client: AsyncClient, tmp_path):
        # write never overwrites (deepagents refuses), so there's nothing to gate here — the
        # agent just sees the same "already exists" error it always would.
        (tmp_path / "relatorio.md").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))

        result = await gate.awrite("/relatorio.md", "novo conteúdo")

        assert result.error is not None
        assert "pending_confirmation" not in result.error
        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "original"

    async def test_edit_of_existing_file_is_parked(self, client: AsyncClient, tmp_path):
        (tmp_path / "nota.txt").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))

        result = await gate.aedit("/nota.txt", "original", "editado")

        assert result.error is not None
        assert "pending_confirmation" in result.error
        assert (tmp_path / "nota.txt").read_text(encoding="utf-8") == "original"

    async def test_edit_of_nonexistent_file_is_not_gated_and_fails_naturally(self, client: AsyncClient, tmp_path):
        gate = _gate(str(tmp_path))

        result = await gate.aedit("/nao-existe.txt", "old", "new")

        assert result.error is not None
        assert "pending_confirmation" not in result.error

    async def test_sync_edit_is_not_gated(self, tmp_path):
        # The sync path applies immediately (see write_gate.py's module docstring) — it's never
        # what the live agent's async graph invocation calls, only direct/sync callers.
        (tmp_path / "nota.txt").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))

        result = gate.edit("/nota.txt", "original", "editado")

        assert result.error is None
        assert (tmp_path / "nota.txt").read_text(encoding="utf-8") == "editado"


class TestFileMutationConfirmationFlow:
    async def test_confirm_applies_the_edit_and_versions_it(self, client: AsyncClient, tmp_path):
        from src.app.init import hitl_service, pending_action_repository

        (tmp_path / "relatorio.md").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))
        await gate.aedit("/relatorio.md", "original", "novo conteúdo")
        pending = await pending_action_repository.list_pending(1)
        assert len(pending) == 1

        await hitl_service.confirm(pending[0].id, 1)

        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "novo conteúdo"
        # The prior content was preserved as a recoverable version (#55), same as any edit.
        assert len(versions_for(str(tmp_path), "relatorio.md")) == 1

    async def test_reject_leaves_file_unchanged_permanently(self, client: AsyncClient, tmp_path):
        from src.app.core.hitl import ConfirmationError
        from src.app.init import hitl_service, pending_action_repository

        (tmp_path / "relatorio.md").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))
        await gate.aedit("/relatorio.md", "original", "novo conteúdo")
        pending = await pending_action_repository.list_pending(1)

        await hitl_service.reject(pending[0].id, 1)

        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "original"
        with pytest.raises(ConfirmationError):
            await hitl_service.confirm(pending[0].id, 1)

    async def test_confirm_over_http(self, client: AsyncClient, tmp_path):
        from src.app.init import pending_action_repository

        (tmp_path / "relatorio.md").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))
        await gate.aedit("/relatorio.md", "original", "novo conteúdo")
        pending = await pending_action_repository.list_pending(1)

        reg = await client.post(
            "/api/v1/auth/register", json={"email": "gate-user@example.com", "password": "TestPass123!"}
        )
        token = reg.json()["token"]["access_token"]
        resp = await client.post(f"/api/v1/hitl/{pending[0].id}/confirm", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200, resp.text
        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "novo conteúdo"

    async def test_non_owner_cannot_confirm_or_reject(self, client: AsyncClient, tmp_path):
        from src.app.init import pending_action_repository

        # First registered user on the fresh per-test DB becomes id 1 — matches _gate()'s default
        # user_id, i.e. the pending action's real owner (mirrors test_hitl.py's own pattern).
        await client.post("/api/v1/auth/register", json={"email": "gate-owner@example.com", "password": "TestPass123!"})
        reg = await client.post(
            "/api/v1/auth/register", json={"email": "gate-attacker@example.com", "password": "TestPass123!"}
        )
        attacker_token = reg.json()["token"]["access_token"]

        (tmp_path / "relatorio.md").write_text("original", encoding="utf-8")
        gate = _gate(str(tmp_path))
        await gate.aedit("/relatorio.md", "original", "novo conteúdo")
        pending = await pending_action_repository.list_pending(1)

        headers = {"Authorization": f"Bearer {attacker_token}"}
        confirm_resp = await client.post(f"/api/v1/hitl/{pending[0].id}/confirm", headers=headers)
        reject_resp = await client.post(f"/api/v1/hitl/{pending[0].id}/reject", headers=headers)

        assert confirm_resp.status_code == 403
        assert reject_resp.status_code == 403
        assert (tmp_path / "relatorio.md").read_text(encoding="utf-8") == "original"
