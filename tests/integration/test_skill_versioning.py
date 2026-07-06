"""Integration tests for skill versioning + approval gate (#17, RF-10).

- Status machine: draft → in_review → approved (and back to draft on edit / send-back).
- Only approved skills materialize into an agent.
- Every content edit snapshots an immutable version.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create(client, token, name="S"):
    resp = await client.post("/api/v1/skills", json={"name": name, "body": "v1"}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _approve(client, token, skill_id):
    await client.post(f"/api/v1/skills/{skill_id}/status", json={"status": "in_review"}, headers=_auth(token))
    r = await client.post(f"/api/v1/skills/{skill_id}/status", json={"status": "approved"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


class TestStatusMachine:
    def test_transition_rules(self):
        from src.app.core.skill.skill_status import can_transition

        assert can_transition("draft", "in_review") is True
        assert can_transition("in_review", "approved") is True
        assert can_transition("approved", "draft") is True
        assert can_transition("draft", "approved") is False   # must be reviewed first
        assert can_transition("draft", "draft") is False
        assert can_transition("approved", "nonsense") is False

    async def test_new_skill_is_draft(self, client: AsyncClient, user_token):
        skill = await _create(client, user_token)
        assert skill["status"] == "draft"
        assert skill["version"] == 1

    async def test_illegal_transition_is_409(self, client: AsyncClient, user_token):
        skill = await _create(client, user_token)
        resp = await client.post(
            f"/api/v1/skills/{skill['id']}/status", json={"status": "approved"}, headers=_auth(user_token)
        )
        assert resp.status_code == 409

    async def test_full_approval_flow(self, client: AsyncClient, user_token):
        skill = await _create(client, user_token)
        approved = await _approve(client, user_token, skill["id"])
        assert approved["status"] == "approved"

    async def test_edit_resets_to_draft_and_bumps_version(self, client: AsyncClient, user_token):
        skill = await _create(client, user_token)
        await _approve(client, user_token, skill["id"])
        edited = await client.patch(
            f"/api/v1/skills/{skill['id']}", json={"body": "v2"}, headers=_auth(user_token)
        )
        assert edited.status_code == 200
        assert edited.json()["status"] == "draft"   # re-approval required
        assert edited.json()["version"] == 2


class TestVersionHistory:
    async def test_edits_accumulate_versions(self, client: AsyncClient, user_token):
        skill = await _create(client, user_token)
        await client.patch(f"/api/v1/skills/{skill['id']}", json={"body": "v2"}, headers=_auth(user_token))
        await client.patch(f"/api/v1/skills/{skill['id']}", json={"body": "v3"}, headers=_auth(user_token))

        resp = await client.get(f"/api/v1/skills/{skill['id']}/versions", headers=_auth(user_token))
        assert resp.status_code == 200
        versions = resp.json()
        assert [v["version"] for v in versions] == [3, 2, 1]  # newest first
        assert versions[0]["body"] == "v3"


class TestApprovalGate:
    async def test_only_approved_skill_materializes(self, client: AsyncClient, user_token):
        from src.app.api.v1.data_agent import _materialize_agent_skills

        skill = await _create(client, user_token)
        # Draft: not loaded.
        assert await _materialize_agent_skills(agent_id=1, owner_id=1, skill_ids=[skill["id"]]) is None

        # Approved: loaded.
        await _approve(client, user_token, skill["id"])
        base = await _materialize_agent_skills(agent_id=1, owner_id=1, skill_ids=[skill["id"]])
        assert base is not None
