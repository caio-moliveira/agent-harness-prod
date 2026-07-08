"""Text normalization for literal document search.

One function, applied to BOTH the stored page text and the query at search time, so accent, case,
and typographic ordinals stop mattering — the agent writes a term naturally and never discovers
there was an encoding problem underneath. Kept portable (pure Python, no DB extension) so it works
identically on SQLite (tests) and Postgres.
"""

import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Fold ``text`` to an accent-, case-, and ordinal-insensitive form for literal matching.

    NFKD decomposition maps typographic forms to plain ASCII (e.g. ``nº`` → ``no``, ``á`` → ``a``),
    combining marks are dropped, case is folded, and runs of whitespace collapse to a single space.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WS.sub(" ", stripped.casefold()).strip()
