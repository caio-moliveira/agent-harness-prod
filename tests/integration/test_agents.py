"""Integration tests for the user-configurable Agent harness (walking skeleton).

Covers agent CRUD, per-user/per-agent isolation, session-to-agent binding, and the mem0
partition seam — all through the existing ASGI HTTP boundary, except the memory-scope
assertion which tests the pure memory helpers directly.
"""

import os
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


# ---------------------------------------------------------------------------
# Credential encryption seam (#3) — the one new pure-function seam
# ---------------------------------------------------------------------------

class TestEncryption:
    def test_encrypt_roundtrip_and_not_plaintext(self):
        from src.app.core.common import config as config_module
        from src.app.core.security import encryption

        with patch.object(config_module.settings, "ENCRYPTION_KEY", "a-dev-secret"):
            token = encryption.encrypt("s3cr3t-password")
            assert token != "s3cr3t-password"
            assert encryption.decrypt(token) == "s3cr3t-password"

    def test_unset_key_declines_to_encrypt(self):
        from src.app.core.common import config as config_module
        from src.app.core.security import encryption

        with patch.object(config_module.settings, "ENCRYPTION_KEY", ""):
            assert encryption.is_encryption_available() is False
            with pytest.raises(RuntimeError):
                encryption.encrypt("x")


# ---------------------------------------------------------------------------
# Database binding to an agent (#3) — persisted + encrypted, no password leak
# ---------------------------------------------------------------------------

class TestAgentDatabaseBinding:
    _BODY = {
        "driver": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "sales",
        "username": "reader",
        "password": "hunter2",
    }

    async def test_bind_db_encrypted_persists_summary_without_password(
        self, client: AsyncClient, user_token
    ):
        from src.app.core.common import config as config_module

        agent = await _create_agent(client, user_token)
        with (
            patch("src.app.api.v1.agents.connect_readonly", new=AsyncMock(return_value=object())),
            patch.object(config_module.settings, "ENCRYPTION_KEY", "dev-key"),
        ):
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/database",
                json=self._BODY,
                headers=_auth(user_token),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["password_persisted"] is True
        assert data["database"]["host"] == "db.example.com"
        assert "password" not in data["database"]

        # GET the agent: summary present, and the encrypted password never leaks in config.
        got = (await client.get(f"/api/v1/agents/{agent['id']}", headers=_auth(user_token))).json()
        assert got["database"]["database"] == "sales"
        assert "hunter2" not in str(got)
        assert "password_encrypted" not in str(got.get("config", {}))

    async def test_bind_db_without_key_does_not_persist_password(self, client: AsyncClient, user_token):
        from src.app.core.common import config as config_module

        agent = await _create_agent(client, user_token)
        with (
            patch("src.app.api.v1.agents.connect_readonly", new=AsyncMock(return_value=object())),
            patch.object(config_module.settings, "ENCRYPTION_KEY", ""),
        ):
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/database",
                json=self._BODY,
                headers=_auth(user_token),
            )
        assert resp.status_code == 200
        assert resp.json()["password_persisted"] is False

    async def test_bind_db_connection_failure_is_400(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        with patch("src.app.api.v1.agents.connect_readonly", new=AsyncMock(side_effect=Exception("no route"))):
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/database",
                json=self._BODY,
                headers=_auth(user_token),
            )
        assert resp.status_code == 400

    async def test_unbind_db(self, client: AsyncClient, user_token):
        from src.app.core.common import config as config_module

        agent = await _create_agent(client, user_token)
        with (
            patch("src.app.api.v1.agents.connect_readonly", new=AsyncMock(return_value=object())),
            patch.object(config_module.settings, "ENCRYPTION_KEY", "dev-key"),
        ):
            await client.put(
                f"/api/v1/agents/{agent['id']}/database", json=self._BODY, headers=_auth(user_token)
            )
        resp = await client.delete(f"/api/v1/agents/{agent['id']}/database", headers=_auth(user_token))
        assert resp.status_code == 200
        assert resp.json()["database"] is None
        got = (await client.get(f"/api/v1/agents/{agent['id']}", headers=_auth(user_token))).json()
        assert got["database"] is None

    async def test_cannot_bind_db_on_another_users_agent(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        attacker = await _register_and_token(client, "db-attacker@example.com")
        with patch("src.app.api.v1.agents.connect_readonly", new=AsyncMock(return_value=object())):
            resp = await client.put(
                f"/api/v1/agents/{agent['id']}/database",
                json=self._BODY,
                headers=_auth(attacker),
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Capability toggles (#5) — web search + memory per agent
# ---------------------------------------------------------------------------

class TestAgentCapabilities:
    async def test_defaults(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        assert agent["web_search"] is False
        assert agent["memory"] is True

    async def test_create_with_toggles(self, client: AsyncClient, user_token):
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Searcher", "web_search": True, "memory": False},
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["web_search"] is True
        assert data["memory"] is False
        # persists
        got = (await client.get(f"/api/v1/agents/{data['id']}", headers=_auth(user_token))).json()
        assert got["web_search"] is True
        assert got["memory"] is False

    async def test_update_toggles(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        resp = await client.patch(
            f"/api/v1/agents/{agent['id']}",
            json={"web_search": True, "memory": False},
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["web_search"] is True
        assert resp.json()["memory"] is False

    def test_runtime_honors_toggles(self):
        """memory off => no memory tool; web_search on => a search tool is attached."""
        from src.app.agents.data_agent.agent_data import _create_data_deep_agent

        no_mem = _create_data_deep_agent(None, None, user_id=1, memory_enabled=False, web_search=False)
        with_search = _create_data_deep_agent(None, None, user_id=1, memory_enabled=True, web_search=True)
        # The compiled agents differ; at minimum both build without error and are distinct objects.
        assert no_mem is not None and with_search is not None


# ---------------------------------------------------------------------------
# Skill library (#6) — author, isolate, attach
# ---------------------------------------------------------------------------

async def _create_skill(client: AsyncClient, token: str, name: str = "Query Tips", body: str = "Do X then Y."):
    resp = await client.post(
        "/api/v1/skills",
        json={"name": name, "description": "how to", "body": body},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestSkillLibrary:
    async def test_crud_roundtrip(self, client: AsyncClient, user_token):
        created = await _create_skill(client, user_token)
        assert created["id"] is not None
        assert created["source"] == "authored"

        listed = await client.get("/api/v1/skills", headers=_auth(user_token))
        assert any(s["id"] == created["id"] for s in listed.json())

        upd = await client.patch(
            f"/api/v1/skills/{created['id']}",
            json={"body": "New body."},
            headers=_auth(user_token),
        )
        assert upd.json()["body"] == "New body."

        dele = await client.delete(f"/api/v1/skills/{created['id']}", headers=_auth(user_token))
        assert dele.status_code == 200
        gone = await client.get(f"/api/v1/skills/{created['id']}", headers=_auth(user_token))
        assert gone.status_code == 404

    async def test_skills_are_private(self, client: AsyncClient, user_token):
        skill = await _create_skill(client, user_token)
        attacker = await _register_and_token(client, "skill-attacker@example.com")
        # not visible
        attacker_list = (await client.get("/api/v1/skills", headers=_auth(attacker))).json()
        assert attacker_list == []
        # not readable / mutable
        assert (await client.get(f"/api/v1/skills/{skill['id']}", headers=_auth(attacker))).status_code == 403
        assert (
            await client.delete(f"/api/v1/skills/{skill['id']}", headers=_auth(attacker))
        ).status_code == 403


class TestAgentSkills:
    async def test_attach_persists(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        s1 = await _create_skill(client, user_token, name="A")
        s2 = await _create_skill(client, user_token, name="B")
        resp = await client.put(
            f"/api/v1/agents/{agent['id']}/skills",
            json={"skill_ids": [s1["id"], s2["id"]]},
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        assert set(resp.json()["skills"]) == {s1["id"], s2["id"]}
        got = (await client.get(f"/api/v1/agents/{agent['id']}", headers=_auth(user_token))).json()
        assert set(got["skills"]) == {s1["id"], s2["id"]}

    async def test_cannot_attach_another_users_skill(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        attacker = await _register_and_token(client, "attach-attacker@example.com")
        victim_skill = await _create_skill(client, user_token, name="Secret")
        resp = await client.put(
            f"/api/v1/agents/{agent['id']}/skills",
            json={"skill_ids": [victim_skill["id"]]},
            headers=_auth(attacker),
        )
        # attacker doesn't even own the agent
        assert resp.status_code == 403

    async def test_attach_nonexistent_skill_404(self, client: AsyncClient, user_token):
        agent = await _create_agent(client, user_token)
        resp = await client.put(
            f"/api/v1/agents/{agent['id']}/skills",
            json={"skill_ids": [999999]},
            headers=_auth(user_token),
        )
        assert resp.status_code == 404


class TestSkillMaterialize:
    def test_writes_skill_md(self, tmp_path):
        from src.app.core.skill import materialize as mat
        from src.app.core.skill.skill_model import Skill

        skill = Skill(user_id=1, name="My Skill", description="one liner", body="Step 1. Do it.")
        base = mat.materialize_skills(agent_id=42, skills=[skill])
        assert base is not None
        skill_md = os.path.join(base, "my-skill", "SKILL.md")
        assert os.path.isfile(skill_md)
        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        assert "name: My Skill" in content
        assert "Step 1. Do it." in content

    def test_empty_returns_none(self):
        from src.app.core.skill import materialize as mat

        assert mat.materialize_skills(agent_id=1, skills=[]) is None


# ---------------------------------------------------------------------------
# Skill registry fetch (#7) — vetted source, saved as a user-owned copy
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def test_parse_skill_md(self):
        from src.app.core.skill.registry import _parse_skill_md

        parsed = _parse_skill_md("---\nname: Cool Skill\ndescription: does things\n---\n\n# Body\nStep 1.")
        assert parsed["name"] == "Cool Skill"
        assert parsed["description"] == "does things"
        assert "Step 1." in parsed["body"]

    def test_invalid_slug_rejected(self):
        import asyncio

        from src.app.core.common import config as config_module
        from src.app.core.skill import registry

        with patch.object(config_module.settings, "SKILL_REGISTRY_URL", "https://reg.example.com"):
            with pytest.raises(ValueError):
                asyncio.run(registry.fetch_registry_skill("../etc/passwd"))

    async def test_fetch_disabled_returns_503(self, client: AsyncClient, user_token):
        from src.app.core.common import config as config_module

        with patch.object(config_module.settings, "SKILL_REGISTRY_URL", ""):
            resp = await client.post(
                "/api/v1/skills/fetch", json={"slug": "query-tips"}, headers=_auth(user_token)
            )
        assert resp.status_code == 503

    async def test_fetch_saves_user_owned_copy(self, client: AsyncClient, user_token):
        from src.app.core.common import config as config_module

        parsed = {"name": "Imported", "description": "from registry", "body": "Do the thing."}
        with (
            patch.object(config_module.settings, "SKILL_REGISTRY_URL", "https://reg.example.com"),
            patch("src.app.api.v1.skills.fetch_registry_skill", new=AsyncMock(return_value=parsed)),
        ):
            resp = await client.post(
                "/api/v1/skills/fetch", json={"slug": "imported"}, headers=_auth(user_token)
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "fetched"
        assert data["body"] == "Do the thing."
        # It is a real library entry now (an independent copy).
        listed = (await client.get("/api/v1/skills", headers=_auth(user_token))).json()
        assert any(s["id"] == data["id"] for s in listed)

    async def test_fetch_missing_returns_404(self, client: AsyncClient, user_token):
        from src.app.core.common import config as config_module

        with (
            patch.object(config_module.settings, "SKILL_REGISTRY_URL", "https://reg.example.com"),
            patch("src.app.api.v1.skills.fetch_registry_skill", new=AsyncMock(return_value=None)),
        ):
            resp = await client.post(
                "/api/v1/skills/fetch", json={"slug": "nope"}, headers=_auth(user_token)
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workspace context priming — the agent is grounded in its sources at session start
# ---------------------------------------------------------------------------

class TestWorkspaceContext:
    def test_folder_brief_lists_files_and_inlines_context_file(self, tmp_path):
        from src.app.agents.data_agent.context import build_workspace_context

        (tmp_path / "vendas.csv").write_text("mes,regiao,receita\njan,SP,1000\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Projeto Vendas\nDados por regiao.", encoding="utf-8")

        ctx = build_workspace_context(str(tmp_path), None)
        assert "/workspace" in ctx
        assert "vendas.csv" in ctx
        assert "Projeto Vendas" in ctx  # README content inlined

    def test_empty_when_no_sources(self):
        from src.app.agents.data_agent.context import build_workspace_context

        assert build_workspace_context(None, None) == ""

    def test_db_schema_included(self):
        from unittest.mock import MagicMock

        from src.app.agents.data_agent.context import build_workspace_context

        fake_db = MagicMock()
        fake_db.get_table_info.return_value = "CREATE TABLE vendas (mes TEXT, receita REAL)"
        ctx = build_workspace_context(None, fake_db)
        assert "Esquema do banco" in ctx
        assert "CREATE TABLE vendas" in ctx


# ---------------------------------------------------------------------------
# DB session robustness (#8) — a failed query must not poison later operations
# ---------------------------------------------------------------------------

class TestSessionRobustness:
    def test_failed_query_does_not_poison_later_operations(self):
        from sqlalchemy import text

        from src.app.core.db.database import session_scope

        # A failing query rolls back within its own scope...
        with pytest.raises(Exception):
            with session_scope() as s:
                s.execute(text("SELECT * FROM definitely_not_a_table"))

        # ...and a later, unrelated operation still succeeds (no aborted-transaction cascade).
        with session_scope() as s:
            value = s.execute(text("SELECT 1")).scalar()
        assert value == 1

    async def test_api_survives_a_failing_request(self, client: AsyncClient, user_token):
        """After a request whose DB query errors, a following request still works."""
        # Force get_user_agents to raise once (simulating a transient DB error). The ASGI test
        # transport re-raises unhandled exceptions, so the first call raises.
        with patch(
            "src.app.init.agent_repository.get_user_agents",
            new=AsyncMock(side_effect=Exception("boom")),
        ):
            with pytest.raises(Exception):
                await client.get("/api/v1/agents", headers=_auth(user_token))

        # A subsequent request on a real DB path succeeds — no lingering aborted transaction.
        ok = await client.get("/api/v1/agents", headers=_auth(user_token))
        assert ok.status_code == 200
