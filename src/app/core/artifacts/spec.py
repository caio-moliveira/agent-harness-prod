"""Artifact spec + template: the structured content the renderers turn into Word/PowerPoint.

Content (what the agent produced) is kept separate from presentation (the ``Template``), so the
same spec renders under any organization's visual identity (RF-12). Every ``Claim`` may carry a
``Source`` — provenance from #12/#14 — and a claim WITHOUT one is rendered with a visible marker,
never silently dropped (RF-13).
"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.app.core.provenance import Source


class Claim(BaseModel):
    """One statement in an artifact, optionally attributed to a source."""

    text: str
    source: Optional[Source] = None


class Section(BaseModel):
    """A titled group of claims."""

    heading: str
    claims: List[Claim] = Field(default_factory=list)


class ArtifactSpec(BaseModel):
    """The full structured content of an artifact, independent of visual style."""

    title: str
    subtitle: Optional[str] = None
    sections: List[Section] = Field(default_factory=list)


class Sheet(BaseModel):
    """One worksheet of a spreadsheet: a header row of columns plus tabular data rows."""

    name: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)


class SpreadsheetSpec(BaseModel):
    """The full structured content of a spreadsheet artifact (one or more worksheets)."""

    title: str
    sheets: List[Sheet] = Field(default_factory=list)


class Template(BaseModel):
    """Visual identity applied at render time, decoupled from the content."""

    name: str = "default"
    primary_color: str = "1F4E79"  # hex RGB, no leading '#'
    heading_font: str = "Calibri"
    body_font: str = "Calibri"


_UNSOURCED_MARKER = "[SEM FONTE]"


def claim_suffix(claim: Claim) -> str:
    """The traceability suffix for a claim: its source, or an explicit unsourced marker."""
    if claim.source is not None:
        return f"  [Fonte: {claim.source.render()}]"
    return f"  {_UNSOURCED_MARKER}"


def unsourced_claims(spec: ArtifactSpec) -> List[Claim]:
    """Return every claim in the spec that has no source (for a pre-render integrity check)."""
    return [c for section in spec.sections for c in section.claims if c.source is None]
