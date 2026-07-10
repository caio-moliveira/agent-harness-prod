"""Per-type heading extraction and tree assembly for the document structure builder.

Prose types (pdf/docx/markdown) yield a flat, in-order list of ``RawHeading``s that
``assemble_tree`` nests by level. PDF is the noisy case: ``pdf_candidates`` proposes heading lines
with cheap heuristics (recall over precision), which the LLM refiner then prunes — heuristics alone
confuse a signature line with a real section. Tabular types build their schema tree directly.
"""

import csv
from collections import Counter
from itertools import count
from typing import Iterator, List, Set, Tuple

import re

from src.app.core.ingestion.parsers import ParsedDocument
from src.app.core.structure.models import Candidate, RawHeading, TreeNode

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_ROMAN = re.compile(r"^[IVXLCDM]+\s*[–\-]\s+\S")
_NUMBERED = re.compile(r"^\d+\.\s+\S")


# --------------------------- tree assembly (shared) ---------------------------

def assemble_tree(headings: List[RawHeading], total_units: int) -> List[TreeNode]:
    """Nest a flat, in-order heading list into a tree and assign pre-order ``node_id``s.

    A node's ``end_index`` runs until the next heading of the same or shallower level (so a parent
    spans all its children), or the document's end.
    """
    nodes: List[TreeNode] = []
    for i, h in enumerate(headings):
        end = total_units
        for nxt in headings[i + 1 :]:
            if nxt.level <= h.level:
                end = h.start if nxt.start <= h.start else nxt.start - 1
                break
        nodes.append(TreeNode(title=h.title, start_index=h.start, end_index=max(h.start, end)))

    root: List[TreeNode] = []
    stack: List[Tuple[int, TreeNode]] = []
    for h, node in zip(headings, nodes, strict=True):
        while stack and stack[-1][0] >= h.level:
            stack.pop()
        (stack[-1][1].nodes if stack else root).append(node)
        stack.append((h.level, node))

    _assign_ids(root, count())
    return root


def _assign_ids(nodes: List[TreeNode], counter: Iterator[int]) -> None:
    """Assign zero-padded ``node_id``s in pre-order (depth-first)."""
    for node in nodes:
        node.node_id = f"{next(counter):04d}"
        _assign_ids(node.nodes, counter)


# --------------------------- prose strategies ---------------------------

def markdown_headings(parsed: ParsedDocument) -> Tuple[List[RawHeading], int]:
    """Headings from markdown ``#`` levels; the locator is the line number."""
    text = parsed.sections[0].text if parsed.sections else ""
    lines = text.splitlines()
    headings = [
        RawHeading(title=m.group(2).strip(), level=len(m.group(1)), start=i)
        for i, line in enumerate(lines, start=1)
        if (m := _MD_HEADING.match(line))
    ]
    return headings or [RawHeading(title="conteúdo", level=1, start=1)], max(1, len(lines))


def docx_headings(parsed: ParsedDocument) -> Tuple[List[RawHeading], int]:
    """Each docx block (heading/table) becomes a level-1 node; ``corpo`` (pre-heading body) is skipped.

    python-docx heading depth is flattened by the parser, so the tree is one level deep — enough to
    navigate to a block by its ordinal.
    """
    headings = [
        RawHeading(title=s.location, level=1, start=i)
        for i, s in enumerate(parsed.sections, start=1)
        if s.location != "corpo"
    ]
    return headings or [RawHeading(title="conteúdo", level=1, start=1)], max(1, len(parsed.sections))


def text_headings(parsed: ParsedDocument) -> Tuple[List[RawHeading], int]:
    """Plain text/log/json: no headings — a single node spanning the whole file (line 1..N)."""
    lines = parsed.sections[0].text.splitlines() if parsed.sections else []
    return [RawHeading(title="conteúdo", level=1, start=1)], max(1, len(lines))


# --------------------------- PDF heuristics (refined by LLM) ---------------------------

def pdf_candidates(parsed: ParsedDocument) -> Tuple[List[Candidate], int]:
    """Propose heading lines from a PDF's per-page text (heuristic; refined by the LLM afterwards).

    Recall over precision: catch anything heading-shaped (roman/numbered/UPPERCASE), drop only
    obvious running headers/footers (lines repeated across many pages). The refiner prunes the rest.
    """
    pages = [[ln.strip() for ln in s.text.splitlines() if ln.strip()] for s in parsed.sections]
    running = _running_lines(pages)
    candidates: List[Candidate] = []
    for page_num, lines in enumerate(pages, start=1):
        for line in lines:
            level = _heading_level(line, running)
            if level:
                candidates.append(Candidate(index=len(candidates), text=line, page=page_num, level=level))
    return candidates, max(1, len(pages))


def _running_lines(pages: List[List[str]]) -> Set[str]:
    """Lines repeated across many pages — running headers/footers, not content."""
    freq: Counter = Counter()
    for lines in pages:
        for line in set(lines):
            freq[line] += 1
    limit = max(3, int(0.4 * len(pages)))
    return {line for line, n in freq.items() if n >= limit}


def _heading_level(s: str, running: Set[str]) -> int:
    """Heuristic heading level (1/2) for a line, or 0 if it is not heading-shaped."""
    if not s or len(s) > 90 or s in running or s[-1] in ",;" or ":" in s:
        return 0
    if _ROMAN.match(s):
        return 1
    if _NUMBERED.match(s) and len(s) <= 80:
        return 2
    first = next((c for c in s if c.isalpha()), "")
    if first and first == first.upper() and _uppercase_ratio(s) >= 0.85 and len(s) >= 4 and len(s.split()) <= 9:
        return 1
    return 0


def _uppercase_ratio(s: str) -> float:
    """Fraction of alphabetic characters that are uppercase (0.0 if none)."""
    letters = [c for c in s if c.isalpha()]
    return sum(1 for c in letters if c == c.upper()) / len(letters) if letters else 0.0


# --------------------------- tabular schema (direct) ---------------------------

def xlsx_schema(parsed: ParsedDocument) -> List[TreeNode]:
    """Workbook → one node per sheet, with the sheet's columns as leaf children."""
    sheets = [
        TreeNode(
            title=s.location,
            start_index=i,
            end_index=i,
            nodes=[TreeNode(title=col) for col in _first_row(s.text, ",")],
        )
        for i, s in enumerate(parsed.sections, start=1)
    ]
    _assign_ids(sheets, count())
    return sheets


def tabular_schema(parsed: ParsedDocument, delimiter: str) -> List[TreeNode]:
    """CSV/TSV: a single table node (spanning the whole file) whose children are its columns."""
    text = parsed.sections[0].text if parsed.sections else ""
    end = max(1, len(text.splitlines()))
    root = [TreeNode(title="tabela", start_index=1, end_index=end, nodes=[TreeNode(title=c) for c in _first_row(text, delimiter)])]
    _assign_ids(root, count())
    return root


def _first_row(text: str, delimiter: str) -> List[str]:
    """Column names from the first non-empty row of delimited text (empty list if none)."""
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return []
    return [c.strip() for c in next(csv.reader([line], delimiter=delimiter))]
