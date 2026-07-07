"""Integration tests for continuous learning (#20).

- reflect_preferences: derives an agent's preferred output format / most-used skill from events.
- run_reflection: persists those preferences (retrievable in a later session).
- propose_refinement: a correction turns into a DRAFT skill revision — gated by #17 approval,
  never auto-applied — and records the correction signal.
"""

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestReflection:
    def test_reflect_preferences_pure(self):
        from src.app.core.learning import reflect_preferences
        from src.app.core.session.event_model import SessionEvent

        events = [
            SessionEvent(user_id=1, session_id="s", event_type="artifact_generated", payload={"format": "docx"}),
            SessionEvent(user_id=1, session_id="s", event_type="artifact_generated", payload={"format": "docx"}),
            SessionEvent(user_id=1, session_id="s", event_type="artifact_generated", payload={"format": "pptx"}),
            SessionEvent(user_id=1, session_id="s", event_type="skill_used", payload={"skill": "resumo"}),
        ]
        profile = reflect_preferences(events)
        assert profile["preferred_output_format"] == "docx"  # most common
        assert profile["most_used_skill"] == "resumo"
        assert profile["total_events"] == 4

    async def test_run_reflection_persists_and_is_retrievable(self, client: AsyncClient):
        from src.app.core.learning import PreferenceRepository, run_reflection
        from src.app.core.session.event_repository import SessionEventRepository

        event_repo = SessionEventRepository()
        for fmt in ("docx", "docx", "pptx"):
            await event_repo.record_event(
                user_id=1, session_id=f"s-{uuid.uuid4()}", event_type="artifact_generated",
                agent_id=7, payload={"format": fmt},
            )

        pref_repo = PreferenceRepository()
        profile = await run_reflection(1, 7, event_repo=event_repo, pref_repo=pref_repo)
        assert profile["preferred_output_format"] == "docx"

        # A later session can read the learned preference back.
        stored = await pref_repo.get_all(1, 7)
        assert stored["preferred_output_format"] == "docx"

    async def test_reflected_preferences_formatted_for_injection(self, client: AsyncClient):
        from src.app.core.learning import PreferenceRepository, get_reflected_preferences

        repo = PreferenceRepository()
        await repo.upsert(1, 7, "preferred_output_format", "docx")
        await repo.upsert(1, 7, "total_events", "5")  # bookkeeping — must be omitted from the prompt

        rendered = await get_reflected_preferences(1, 7)
        assert "preferred_output_format: docx" in rendered
        assert "total_events" not in rendered
        # No preferences for another agent → empty string (nothing injected).
        assert await get_reflected_preferences(1, 999) == ""

    async def test_reflection_isolated_per_agent(self, client: AsyncClient):
        from src.app.core.learning import PreferenceRepository, run_reflection
        from src.app.core.session.event_repository import SessionEventRepository

        event_repo = SessionEventRepository()
        await event_repo.record_event(
            user_id=1, session_id="sx", event_type="artifact_generated", agent_id=7, payload={"format": "pptx"}
        )
        await run_reflection(1, 7, event_repo=event_repo, pref_repo=PreferenceRepository())
        # A different agent has no reflected preferences.
        assert await PreferenceRepository().get_all(1, 8) == {}


class TestGatedRefinement:
    async def test_refinement_lands_as_draft_and_records_signal(self, client: AsyncClient, user_token):
        from src.app.core.learning import CorrectionRepository, propose_refinement
        from src.app.core.skill.skill_repository import SkillRepository

        # Create + approve a skill via API (owner is user 1).
        created = (
            await client.post(
                "/api/v1/skills", json={"name": "Resumo", "body": "v1"},
                headers={"Authorization": f"Bearer {user_token}"},
            )
        ).json()
        skill_id = created["id"]
        await client.post(f"/api/v1/skills/{skill_id}/status", json={"status": "in_review"},
                          headers={"Authorization": f"Bearer {user_token}"})
        await client.post(f"/api/v1/skills/{skill_id}/status", json={"status": "approved"},
                          headers={"Authorization": f"Bearer {user_token}"})

        # A correction proposes a refinement.
        corr_repo = CorrectionRepository()
        refined = await propose_refinement(
            user_id=1, agent_id=7, skill_id=skill_id, proposed_body="v2 corrigido",
            correction_note="faltou a seção de riscos", skill_repo=SkillRepository(), correction_repo=corr_repo,
        )
        # Gated: the refinement is a draft, NOT auto-approved.
        assert refined.status == "draft"
        assert refined.version == 2
        assert refined.body == "v2 corrigido"

        # The correction signal was captured.
        signals = await corr_repo.list_for_agent(1, 7)
        assert len(signals) == 1
        assert "riscos" in signals[0].note

    async def test_refined_skill_not_loaded_until_reapproved(self, client: AsyncClient, user_token):
        from src.app.api.v1.data_agent import _materialize_agent_skills
        from src.app.core.learning import propose_refinement

        created = (
            await client.post(
                "/api/v1/skills", json={"name": "Analise", "body": "v1"},
                headers={"Authorization": f"Bearer {user_token}"},
            )
        ).json()
        sid = created["id"]
        for status in ("in_review", "approved"):
            await client.post(f"/api/v1/skills/{sid}/status", json={"status": status},
                              headers={"Authorization": f"Bearer {user_token}"})
        # Approved → loads.
        assert await _materialize_agent_skills(agent_id=1, owner_id=1, skill_ids=[sid]) is not None

        # Refine → back to draft → no longer loads.
        await propose_refinement(user_id=1, agent_id=1, skill_id=sid, proposed_body="v2")
        assert await _materialize_agent_skills(agent_id=1, owner_id=1, skill_ids=[sid]) is None
