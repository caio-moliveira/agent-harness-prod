"""Data shapes for the per-file document structure tree (PageIndex-style, built locally).

The tree stores *structure*, not text: each node is a located section with a page/line span. The
node's content is read on demand from those coordinates (see the navigation tools), so the stored
JSON stays small.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class TreeNode(BaseModel):
    """One node of a document's structure tree: a located section and its children.

    ``start_index``/``end_index`` are the section's span in the document's own locator units: page
    numbers for PDF, parsed-section ordinals for docx/xlsx, line numbers for markdown, and ``1`` for
    a plain-text file with no headings. The content tools resolve these coordinates back to text.
    """

    title: str
    node_id: str = ""
    start_index: Optional[int] = None
    end_index: Optional[int] = None
    nodes: List["TreeNode"] = Field(default_factory=list)


class DocumentTree(BaseModel):
    """A whole file's structure: catalog fields plus the hierarchical ``structure``."""

    doc_id: str
    doc_name: str
    doc_type: str
    structure: List[TreeNode] = Field(default_factory=list)


class RawHeading(BaseModel):
    """A heading detected in a document, before nesting: title, level, and start locator."""

    title: str
    level: int
    start: int


class Candidate(BaseModel):
    """A possible heading line handed to the LLM refiner for a keep/drop + level decision."""

    index: int
    text: str
    page: int
    level: int
