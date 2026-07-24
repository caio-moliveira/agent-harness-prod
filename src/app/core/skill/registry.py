"""Fetch skills from a single vetted registry (allow-listed base URL).

Only ``SKILL_REGISTRY_URL`` may be fetched — never an arbitrary URL. A fetched skill is parsed
from its ``SKILL.md`` (frontmatter ``name``/``description`` + markdown body) and returned so the
API can save it as the user's own copy, decoupled from later upstream changes.

Registry layout expected:
- ``<base>/index.json`` -> a JSON list of ``{"slug", "name", "description"}``
- ``<base>/<slug>/SKILL.md`` -> the skill document
"""

import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.app.core.common.config import settings
from src.app.core.common.logging import logger

_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_FETCH_TIMEOUT = 10.0


def is_registry_enabled() -> bool:
    """True when a skill registry base URL is configured."""
    return bool(settings.SKILL_REGISTRY_URL)


def _base() -> str:
    return settings.SKILL_REGISTRY_URL.rstrip("/")


def _assert_same_host(url: str) -> None:
    """Defense in depth: the resolved URL must stay on the configured registry host."""
    if urlparse(url).netloc != urlparse(_base()).netloc:
        raise ValueError("refusing to fetch outside the configured skill registry")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp


def parse_skill_md(text: str) -> dict:
    """Parse a SKILL.md into name/description/body without a YAML dependency."""
    name, description, body = "", "", text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if match:
        front, body = match.group(1), match.group(2)
        for line in front.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "name":
                name = value
            elif key == "description":
                description = value
    return {"name": name, "description": description, "body": body.strip()}


async def fetch_registry_index() -> list[dict]:
    """Return the registry's list of available skills."""
    url = f"{_base()}/index.json"
    _assert_same_host(url)
    resp = await _get(url)
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("registry index.json must be a JSON array")
    return [
        {"slug": i.get("slug", ""), "name": i.get("name", ""), "description": i.get("description", "")}
        for i in data
        if isinstance(i, dict) and _SLUG_RE.match(str(i.get("slug", "")))
    ]


async def fetch_registry_skill(slug: str) -> Optional[dict]:
    """Fetch and parse one skill by slug. Returns name/description/body, or None on 404."""
    if not _SLUG_RE.match(slug):
        raise ValueError("invalid skill slug")
    url = f"{_base()}/{slug}/SKILL.md"
    _assert_same_host(url)
    try:
        resp = await _get(url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        logger.warning("skill_registry_fetch_failed", slug=slug, status=e.response.status_code)
        raise
    parsed = parse_skill_md(resp.text)
    if not parsed["name"]:
        parsed["name"] = slug
    return parsed
