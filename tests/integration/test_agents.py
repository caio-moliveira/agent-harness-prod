"""Integration tests for the user-configurable Agent harness (walking skeleton).

Covers agent CRUD, per-user/per-agent isolation, session-to-agent binding, and the mem0
partition seam — all through the existing ASGI HTTP boundary, except the memory-scope
assertion which tests the pure memory helpers directly.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.integration.conftest import TEST_PASSWORD

pytestmark = pytest.mark.asyncio


async def _register_and_token(client: AsyncClient, email: str) -> str:
    """Register a fresh user and return their user-scoped bearer token."""
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": TEST_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["token"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_agent(client: AsyncClient, token: str, name: str = "My Agent", prompt: str = "Be helpful.") -> dict:
    resp = await client.post(
        "/api/v1/agents",
        json={"name": name, "system_prompt": prompt},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

class TestAgentCrud:
    async def test_create_and_get(self, client: AsyncClient, user_token):
        created = await _create_agent(client, user_token, name="Research Bot", prompt="You research things.")
        assert created["id"] is not None
        assert created["name"] == "Research Bot"
        assert created["system_prompt"] == "You research things."

        got = await client.get(f"/api/v1/agents/{created['id']}", headers=_auth(user_token))
        assert got.status_code == 200
        assert got.json()["name"] == "Research Bot"

    async def test_list_only_own_agents(self, client: AsyncClient, user_token):
        await _create_agent(client, user_token, name="A")
        await _create_agent(client, user_token, name="B")
        resp = await client.get("/api/v1/agents", headers=_auth(user_token))
        assert resp.status_code == 200
        names = {a["name"] for a in resp.json()}
        assert {"A", "B"} <= names

    async def test_update(self, client: AsyncClient, user_token):
        created = await _create_agent(client, user_token)
        resp = await client.patch(
            f"/api/v1/agents/{created['id']}",
            json={"name": "Renamed", "system_prompt": "New prompt."},
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["system_prompt"] == "New prompt."

    async def test_delete(self, client: AsyncClient, user_token):
        created = await _create_agent(client, user_token)
        resp = await client.delete(f"/api/v1/agents/{created['id']}", headers=_auth(user_token))
        assert resp.status_code == 200
        gone = await client.get(f"/api/v1/agents/{created['id']}", headers=_auth(user_token))
        assert gone.status_code == 404

    async def test_create_requires_auth(self, client: AsyncClient):
        resp = await client.post("/api/v1/agents", json={"name": "X"})
        assert resp.status_code == 401

    async def test_create_rejects_empty_name(self, client: AsyncClient, user_token):
        resp = await client.post("/api/v1/agents", json={"name": "   "}, headers=_auth(user_token))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Per-user isolation (403 across owners)
# ---------------------------------------------------------------------------

class TestAgentIsolation:
    async def test_cannot_read_another_users_agent(self, client: AsyncClient, user_token):
        owner_agent = await _create_agent(client, user_token, name="Private")
        attacker = await _register_and_token(client, "attacker@example.com")

        resp = await client.get(f"/api/v1/agents/{owner_agent['id']}", headers=_auth(attacker))
        assert resp.status_code == 403

    async def test_cannot_update_another_users_agent(self, client: AsyncClient, user_token):
        owner_agent = await _create_agent(client, user_token)
        attacker = await _register_and_token(client, "attacker2@example.com")

        resp = await client.patch(
            f"/api/v1/agents/{owner_agent['id']}",
            json={"name": "pwned"},
            headers=_auth(attacker),
        )
        assert resp.status_code == 403

    async def test_cannot_delete_another_users_agent(self, client: AsyncClient, user_token):
        owner_agent = await _create_agent(client, user_token)
        attacker = await _register_and_token(client, "attacker3@example.com")

        resp = await client.delete(f"/api/v1/agents/{owner_agent['id']}", headers=_auth(attacker))
        assert resp.status_code == 403

    async def test_attacker_does_not_see_owner_agent_in_list(self, client: AsyncClient, user_token):
        await _create_agent(client, user_token, name="OwnerOnly")
        attacker = await _register_and_token(client, "attacker4@example.com")
        resp = await client.get("/api/v1/agents", headers=_auth(attacker))
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Session <-> agent binding
# ---------------------------------------------------------------------------

class TestSessionAgentBinding:
    async def test_session_bound_to_agent(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        resp = await client.post(
            f"/api/v1/auth/session?agent_id={agent['id']}",
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == agent["id"]

    async def test_sessions_list_filtered_by_agent(self, client: AsyncClient, user_token):
        agent_a = await _create_agent(client, user_token, name="A")
        agent_b = await _create_agent(client, user_token, name="B")
        await client.post(f"/api/v1/auth/session?agent_id={agent_a['id']}", headers=_auth(user_token))
        await client.post(f"/api/v1/auth/session?agent_id={agent_b['id']}", headers=_auth(user_token))

        resp = await client.get(f"/api/v1/auth/sessions?agent_id={agent_a['id']}", headers=_auth(user_token))
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["agent_id"] == agent_a["id"]

    async def test_cannot_bind_session_to_another_users_agent(self, client: AsyncClient, user_token):
        owner_agent = await _create_agent(client, user_token)
        attacker = await _register_and_token(client, "attacker5@example.com")
        resp = await client.post(
            f"/api/v1/auth/session?agent_id={owner_agent['id']}",
            headers=_auth(attacker),
        )
        assert resp.status_code == 403

    async def test_bind_nonexistent_agent_404(self, client: AsyncClient, user_token):
        resp = await client.post("/api/v1/auth/session?agent_id=999999", headers=_auth(user_token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# mem0 partition seam — memory scoped by (user_id, agent_id)
# ---------------------------------------------------------------------------

class TestMemoryPartition:
    async def test_retrieval_scoped_by_agent(self):
        from src.app.core.memory import memory as memory_module

        fake = AsyncMock()
        fake.search = AsyncMock(return_value={"results": []})
        with patch.object(memory_module, "get_memory_instance", AsyncMock(return_value=fake)):
            await memory_module.get_relevant_memory(user_id=7, query="q", agent_id=42)

        fake.search.assert_awaited_once()
        kwargs = fake.search.await_args.kwargs
        assert kwargs["user_id"] == "7"
        assert kwargs["agent_id"] == "42"

    async def test_write_scoped_by_agent(self):
        from src.app.core.memory import memory as memory_module

        fake = AsyncMock()
        fake.add = AsyncMock(return_value=None)
        with patch.object(memory_module, "get_memory_instance", AsyncMock(return_value=fake)):
            await memory_module.update_memory(
                user_id=7, messages=[{"role": "user", "content": "hi"}], metadata={}, agent_id=42
            )

        fake.add.assert_awaited_once()
        kwargs = fake.add.await_args.kwargs
        assert kwargs["user_id"] == "7"
        assert kwargs["agent_id"] == "42"

    async def test_no_agent_scope_when_agent_id_none(self):
        from src.app.core.memory import memory as memory_module

        fake = AsyncMock()
        fake.search = AsyncMock(return_value={"results": []})
        with patch.object(memory_module, "get_memory_instance", AsyncMock(return_value=fake)):
            await memory_module.get_relevant_memory(user_id=7, query="q")

        kwargs = fake.search.await_args.kwargs
        assert "agent_id" not in kwargs


# ---------------------------------------------------------------------------
# System-prompt composition — a custom persona must NOT drop tool guidance
# ---------------------------------------------------------------------------

class TestSystemPromptComposition:
    def test_custom_prompt_keeps_persona_and_gains_tool_guidance(self):
        from src.app.agents.data_agent.agent_data import _compose_system_prompt

        composed = _compose_system_prompt("Você é o Zé, um analista rabugento.")
        # persona preserved
        assert "Zé" in composed
        # tool mechanics always present so the model knows it can read files
        assert "/workspace" in composed
        assert "read_file" in composed

    def test_empty_prompt_uses_bundled_default(self):
        from src.app.agents.data_agent.agent_data import _compose_system_prompt, load_system_prompt

        assert _compose_system_prompt("") == load_system_prompt()
        assert _compose_system_prompt(None) == load_system_prompt()


# ---------------------------------------------------------------------------
# Folder binding to an agent (#4) — persisted, re-validated vs allowed roots
# ---------------------------------------------------------------------------

class TestAgentFolderBinding:
    def _patch_roots(self, tmp_path):
        """Patch settings so a real temp dir is a valid grantable root (no Docker needed)."""
        from src.app.core.common import config as config_module

        return (
            patch.object(config_module.settings, "SANDBOX_ENABLED", True),
            patch.object(config_module.settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)]),
        )

    async def test_bind_folder_persists(self, client: AsyncClient, user_token, tmp_path):
        agent = await _create_agent(client, user_token)
        sub = tmp_path / "data"
        sub.mkdir()
        p_enabled, p_roots = self._patch_roots(tmp_path)
        with p_enabled, p_roots:
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/folder",
                json={"path": str(sub)},
                headers=_auth(user_token),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["folder"].endswith("data")

        # The binding survives on the agent (visible on GET).
        got = await client.get(f"/api/v1/agents/{agent['id']}", headers=_auth(user_token))
        assert got.json()["folder"] is not None
        assert got.json()["folder"].endswith("data")

    async def test_bind_folder_outside_roots_rejected(self, client: AsyncClient, user_token, tmp_path):
        agent = await _create_agent(client, user_token)
        outside = tmp_path.parent / "outside_root"
        outside.mkdir(exist_ok=True)
        p_enabled, p_roots = self._patch_roots(tmp_path)  # allow only tmp_path
        with p_enabled, p_roots:
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/folder",
                json={"path": str(outside)},
                headers=_auth(user_token),
            )
            assert resp.status_code == 403

    async def test_bind_nonexistent_dir_rejected(self, client: AsyncClient, user_token, tmp_path):
        agent = await _create_agent(client, user_token)
        p_enabled, p_roots = self._patch_roots(tmp_path)
        with p_enabled, p_roots:
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/folder",
                json={"path": str(tmp_path / "does_not_exist")},
                headers=_auth(user_token),
            )
            assert resp.status_code == 400

    async def test_unbind_folder(self, client: AsyncClient, user_token, tmp_path):
        agent = await _create_agent(client, user_token)
        sub = tmp_path / "d"
        sub.mkdir()
        p_enabled, p_roots = self._patch_roots(tmp_path)
        with p_enabled, p_roots:
            await client.put(
                f"/api/v1/agents/{agent['id']}/folder",
                json={"path": str(sub)},
                headers=_auth(user_token),
            )
        resp = await client.delete(f"/api/v1/agents/{agent['id']}/folder", headers=_auth(user_token))
        assert resp.status_code == 200
        assert resp.json()["folder"] is None
        got = await client.get(f"/api/v1/agents/{agent['id']}", headers=_auth(user_token))
        assert got.json()["folder"] is None

    async def test_cannot_bind_folder_on_another_users_agent(self, client: AsyncClient, user_token, tmp_path):
        agent = await _create_agent(client, user_token)
        sub = tmp_path / "x"
        sub.mkdir()
        attacker = await _register_and_token(client, "folder-attacker@example.com")
        p_enabled, p_roots = self._patch_roots(tmp_path)
        with p_enabled, p_roots:
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/folder",
                json={"path": str(sub)},
                headers=_auth(attacker),
            )
            assert resp.status_code == 403
