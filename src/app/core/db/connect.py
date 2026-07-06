"""Build read-only database connections from connection parts.

Shared by the per-session ``connect-db`` endpoint, the per-agent database binding, and the
chat-time runtime that materializes a bound database. Keeps URL assembly in one place.
"""

import asyncio
from typing import Optional
from urllib.parse import quote_plus

from langchain_community.utilities import SQLDatabase


def build_db_url(
    driver: str,
    username: str,
    password: str,
    host: str,
    port: int,
    database: str,
    sslmode: Optional[str] = None,
) -> str:
    """Assemble a URL-encoded SQLAlchemy connection URL from parts."""
    user = quote_plus(username)
    pwd = quote_plus(password)
    url = f"{driver}://{user}:{pwd}@{host}:{port}/{database}"
    if sslmode:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode={sslmode}"
    return url


async def connect_readonly(url: str) -> SQLDatabase:
    """Open a SQLDatabase off the event loop (blocking driver connect)."""
    return await asyncio.to_thread(SQLDatabase.from_uri, url, None, sample_rows_in_table_info=3)
