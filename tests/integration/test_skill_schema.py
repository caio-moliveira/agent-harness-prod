"""Integration tests for structured skill fields (#16, RF-08).

Skills gain ``when_to_use``, ``sources``, ``steps``, ``output_format`` beyond free-form body.
Seams: the HTTP CRUD boundary (create/get/update echo + persist the fields) and materialization
(the structured fields become headed sections in the generated SKILL.md).
"""

import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestStructuredSkillCrud:
    async def test_create_persists_structured_fields(self, client: AsyncClient, user_token):
        resp = await client.post(
            "/api/v1/skills",
            json={
                "name": "Resumo de Contrato",
                "description": "resumir contratos",
                "when_to_use": "quando pedirem resumo de contrato",
                "sources": "documento do contrato; histórico de disputas",
                "steps": "identificar partes; prazo; riscos",
                "output_format": "relatório Word com seções fixas",
            },
            headers=_auth(user_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["when_to_use"].startswith("quando")
        assert "riscos" in data["steps"]

        got = (await client.get(f"/api/v1/skills/{data['id']}", headers=_auth(user_token))).json()
        assert got["sources"].startswith("documento")
        assert got["output_format"] == "relatório Word com seções fixas"

    async def test_update_structured_field(self, client: AsyncClient, user_token):
        created = (
            await client.post(
                "/api/v1/skills", json={"name": "S", "description": "d"}, headers=_auth(user_token)
            )
        ).json()
        upd = await client.patch(
            f"/api/v1/skills/{created['id']}",
            json={"steps": "passo 1; passo 2"},
            headers=_auth(user_token),
        )
        assert upd.status_code == 200
        assert upd.json()["steps"] == "passo 1; passo 2"


class TestStructuredMaterialize:
    def test_structured_fields_become_sections(self, tmp_path):
        from src.app.core.skill import materialize as mat
        from src.app.core.skill.skill_model import Skill

        skill = Skill(
            user_id=1, name="Contrato", description="d", body="Notas gerais.",
            when_to_use="ao pedir resumo", sources="o contrato", steps="1. identificar partes",
            output_format="relatório Word",
        )
        base = mat.materialize_skills(agent_id=1, skills=[skill])
        with open(os.path.join(base, "contrato", "SKILL.md"), encoding="utf-8") as f:
            content = f.read()

        assert "Notas gerais." in content
        assert "## Quando usar" in content and "ao pedir resumo" in content
        assert "## Passo a passo" in content and "identificar partes" in content
        assert "## Formato de saída" in content and "relatório Word" in content

    def test_empty_structured_fields_are_omitted(self, tmp_path):
        from src.app.core.skill import materialize as mat
        from src.app.core.skill.skill_model import Skill

        skill = Skill(user_id=1, name="Simples", description="d", body="Só o corpo.")
        base = mat.materialize_skills(agent_id=2, skills=[skill])
        with open(os.path.join(base, "simples", "SKILL.md"), encoding="utf-8") as f:
            content = f.read()
        assert "Só o corpo." in content
        assert "## Quando usar" not in content  # no empty sections
