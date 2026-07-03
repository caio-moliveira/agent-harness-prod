# Local development runner for Windows / PowerShell.
# Avoids the make + bash workflow (which doesn't work natively on Windows) and the
# Git Bash MSYS path-mangling that corrupts values like /api/v1.
#
# Usage:
#   .\dev.ps1            # start Postgres (if needed) + the API on :8000
#   .\dev.ps1 -Reload    # same, with hot-reload (needs the 'watchfiles' package)
#
# Prereqs (one-time):
#   winget install -e --id Python.Python.3.13   # signed CPython (Smart App Control friendly)
#   uv venv --python "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
#   uv sync
#   Docker Desktop running

param([switch]$Reload)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. Ensure .env.development exists
if (-not (Test-Path ".env.development")) {
    Copy-Item ".env.example" ".env.development"
    Write-Host "Created .env.development from .env.example — fill OPENAI_API_KEY and JWT_SECRET_KEY." -ForegroundColor Yellow
}

# 2. Ensure Postgres is up (pgvector). Config self-loads .env.development, so the app
#    reads POSTGRES_* on its own — we only need the container running.
$dbState = docker inspect --format='{{.State.Health.Status}}' agent-harness-db 2>$null
if ($dbState -ne "healthy") {
    Write-Host "Starting Postgres (pgvector)..." -ForegroundColor Cyan
    docker compose up -d db | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if ((docker inspect --format='{{.State.Health.Status}}' agent-harness-db 2>$null) -eq "healthy") {
            Write-Host "Postgres healthy." -ForegroundColor Green; break
        }
    }
}

# 3. Run the API via run_local.py, which forces the Windows SelectorEventLoop
#    (psycopg's async pool / LangGraph checkpointer cannot use the default ProactorEventLoop).
#    Only APP_ENV is passed via env (a simple value, no MSYS path mangling); everything else is
#    loaded from .env.development by src/app/core/common/config.py.
$env:APP_ENV = "development"
$py = ".\.venv\Scripts\python.exe"
$runArgs = @("run_local.py")
if ($Reload) { $runArgs += "--reload" }

Write-Host "API -> http://localhost:8000/docs" -ForegroundColor Green
& $py @runArgs
