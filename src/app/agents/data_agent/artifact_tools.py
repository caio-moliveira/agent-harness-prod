"""Artifact-generation tool for the Data Agent (#18 wiring, gated by #19).

Exposes ``gerar_artefato`` so the agent can turn structured, sourced content into a Word (``docx``)
or PowerPoint (``pptx``) deliverable. Producing a deliverable is an outward-facing action, so the
tool does NOT render inline: it parks an ``export_artifact`` request for human confirmation (#19).
The file is rendered only when the owner confirms — and it is at that point that the
``artifact_generated`` event is recorded (#10), feeding the success metrics (#21) and the reflection
pass (#20). The tool is bound to one ``(user_id, agent_id, session_id)`` so the parked action is
attributed and isolated to the session that produced it.
"""

import os
import re
import tempfile
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from src.app.core.artifacts import ArtifactSpec, Claim, Section
from src.app.core.common.logging import logger
from src.app.core.provenance import Source
from src.app.init import hitl_service

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
_SUPPORTED_FORMATS = ("docx", "pptx")


class ClaimInput(BaseModel):
    """One line/statement of the artifact, with optional provenance."""

    texto: str = Field(..., description="Uma afirmação/linha do artefato.")
    fonte: Optional[str] = Field(
        default=None,
        description="Proveniência do dado (ex.: tabela + consulta, ou documento). Omita se não houver — o item será marcado como [SEM FONTE].",
    )


class SectionInput(BaseModel):
    """A titled group of claims in the artifact."""

    titulo: str = Field(..., description="Título da seção.")
    itens: list[ClaimInput] = Field(default_factory=list, description="Afirmações desta seção.")


def _to_spec(titulo: str, subtitulo: Optional[str], secoes: list[SectionInput]) -> ArtifactSpec:
    """Map the LLM-friendly tool input into a rendered ``ArtifactSpec`` (preserving sources)."""
    sections = [
        Section(
            heading=s.titulo,
            claims=[
                Claim(text=i.texto, source=Source(kind="doc_chunk", document=i.fonte) if i.fonte else None)
                for i in s.itens
            ],
        )
        for s in secoes
    ]
    return ArtifactSpec(title=titulo, subtitle=subtitulo, sections=sections)


def _output_dir(session_id: str, root_dir: Optional[str], writable_folder: bool) -> str:
    """Where the confirmed artifact is written.

    When the session has a *writable* granted folder, write the deliverable there so the user finds
    it alongside their data. Otherwise fall back to a per-session temp dir (a read-only folder must
    never be written to).
    """
    if root_dir and writable_folder:
        return root_dir
    return os.path.join(tempfile.gettempdir(), "agent_harness_artifacts", str(session_id))


def make_artifact_tools(
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: Optional[str],
    root_dir: Optional[str] = None,
    writable_folder: bool = False,
) -> list[BaseTool]:
    """Build the artifact tool bound to one session. Empty list without a user/session context."""
    if user_id is None or not session_id:
        return []

    @tool
    async def gerar_artefato(
        titulo: str,
        formato: str,
        secoes: list[SectionInput],
        subtitulo: Optional[str] = None,
    ) -> str:
        """Gera um relatório em Word (docx) ou PowerPoint (pptx) a partir de conteúdo estruturado.

        Use quando o usuário pedir um relatório, documento ou apresentação. Monte ``secoes`` com
        títulos e itens; em cada item inclua ``fonte`` (a proveniência do dado — ex.: a tabela e a
        consulta que produziram o número, ou o documento lido) sempre que possível: itens sem fonte
        saem marcados como [SEM FONTE] no arquivo. ``formato`` deve ser "docx" ou "pptx".
        """
        fmt = (formato or "").lower().strip()
        if fmt not in _SUPPORTED_FORMATS:
            return f"Formato inválido: '{formato}'. Use 'docx' ou 'pptx'."
        if not secoes:
            return "Nada a gerar: envie ao menos uma seção com itens."

        spec = _to_spec(titulo, subtitulo, secoes)
        base = _output_dir(session_id, root_dir, writable_folder)
        safe = _SAFE_NAME.sub("_", titulo).strip("_") or "artefato"
        path = os.path.join(base, f"{safe}.{fmt}")

        # Producing a deliverable is outward-facing (#19): park it for confirmation instead of
        # rendering inline. The file is written only when the owner confirms (executor renders it).
        try:
            action = await hitl_service.request(
                user_id,
                session_id,
                "export_artifact",
                {"spec": spec.model_dump(mode="json"), "fmt": fmt, "path": path, "agent_id": agent_id},
            )
        except Exception:
            logger.exception("artifact_request_failed", session_id=session_id, fmt=fmt)
            return "Falha ao preparar o artefato. Tente novamente."

        item_count = sum(len(s.itens) for s in secoes)
        return (
            f"Preparei o artefato '{titulo}' ({fmt}, {len(secoes)} seção(ões), {item_count} item(ns)). "
            f"Ele está aguardando sua confirmação em 'Ações pendentes' (id {action.id}): "
            "aprove para gerar o arquivo, ou rejeite para descartar."
        )

    return [gerar_artefato]
