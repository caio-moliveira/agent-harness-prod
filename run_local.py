"""Local dev entrypoint (Windows-friendly).

On Windows the default asyncio loop is the ProactorEventLoop, which psycopg's async
connection pool (used by the LangGraph AsyncPostgresSaver checkpointer) cannot use.
On Linux/Mac this is handled by uvloop, but uvloop does not build on Windows. So we
force the SelectorEventLoop policy BEFORE uvicorn creates the loop.

Usage:
    python run_local.py            # serve on 127.0.0.1:8000
    python run_local.py --reload   # with hot-reload
"""

import asyncio
import sys

import uvicorn

# Force UTF-8 console output before anything logs — Windows consoles default to cp1252 and would
# raise UnicodeEncodeError (dropping log lines) on the non-ASCII characters in the agent's traces.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    reload = "--reload" in sys.argv
    uvicorn.run("src.app.main:app", host="127.0.0.1", port=8000, reload=reload)
