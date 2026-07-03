---
description: Manage the local Postgres (pgvector) Docker service
argument-hint: up | down | logs | psql | reset
---

Manage the project's Postgres service. Action requested: $1 (default: up)

- **up**    → `make db-up`. Waits for the healthcheck, confirms pgvector extension is enabled.
- **down**  → `make db-down` (keeps the data volume).
- **logs**  → `docker compose logs -f db`.
- **psql**  → open a shell: `docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"`.
- **reset** → ⚠️ destroys data: `make db-reset` (down + remove volume + up). Confirm with the user first.

After `up`, verify the extension:
`docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname='vector';"`
