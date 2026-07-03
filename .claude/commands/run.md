---
description: Bring up Postgres and run the API locally, then verify it responds
---

Run this project locally end-to-end:

1. Ensure `.env.development` exists (copy from `.env.example` if missing) and has
   `OPENAI_API_KEY`, `JWT_SECRET_KEY` set. Do NOT print secret values.
2. Start Postgres: `make db-up` and wait until the container healthcheck passes.
3. Start the API: `make dev` (runs uvicorn on :8000 with reload).
4. Verify: `curl http://localhost:8000/api/v1/health` returns healthy with DB status ok.
   Report the health output and the Swagger URL (`http://localhost:8000/docs`).

If the DB connection fails, check `POSTGRES_HOST` in `.env.development` — it must be `localhost`
when running the API on the host (and `db` only when the API itself runs inside compose).
