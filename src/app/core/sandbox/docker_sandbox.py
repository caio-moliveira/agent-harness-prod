"""Docker-backed sandbox for the Deep Agents filesystem/shell tools.

We reuse Deep Agents' sandbox framework: ``BaseSandbox`` implements every file
operation (ls/read/grep/glob/write/edit) on top of a single ``execute()`` shell
call. We only provide ``execute()`` = ``docker exec`` into a locked-down, per-session
container, plus container lifecycle helpers.

Hardening applied to each container: no network, dropped capabilities,
no-new-privileges, read-only root filesystem, a small writable tmpfs, CPU/mem/pids
limits, and the user's folder bind-mounted READ-ONLY at the mount path.
"""

import asyncio
import subprocess
from typing import Optional

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

from src.app.core.common.config import settings
from src.app.core.common.logging import logger

_MAX_OUTPUT_CHARS = 30_000


class DockerSandbox(BaseSandbox):
    """A SandboxBackend whose ``execute`` runs inside a locked-down Docker container."""

    def __init__(self, container_id: str, timeout: Optional[int] = None):
        self._container_id = container_id
        self._timeout = timeout or settings.SANDBOX_EXEC_TIMEOUT

    @property
    def id(self) -> str:
        """Unique identifier for this sandbox (the container id)."""
        return self._container_id

    def execute(self, command: str, *, timeout: Optional[int] = None) -> ExecuteResponse:
        """Run a shell command inside the container and return its combined output."""
        effective_timeout = timeout or self._timeout
        try:
            proc = subprocess.run(  # noqa: S603 - args are a fixed docker invocation
                ["docker", "exec", self._container_id, "sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(output=f"Command timed out after {effective_timeout}s", exit_code=124)

        output = (proc.stdout or "") + (proc.stderr or "")
        truncated = len(output) > _MAX_OUTPUT_CHARS
        if truncated:
            output = output[:_MAX_OUTPUT_CHARS]
        return ExecuteResponse(output=output, exit_code=proc.returncode, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Uploads are not supported for read-only folder sandboxes."""
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download file contents from the container via ``cat`` (best-effort, per-file)."""
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                proc = subprocess.run(  # noqa: S603
                    ["docker", "exec", self._container_id, "cat", path],
                    capture_output=True,
                    timeout=self._timeout,
                )
                if proc.returncode == 0:
                    results.append(FileDownloadResponse(path=path, content=proc.stdout))
                else:
                    results.append(FileDownloadResponse(path=path, error="file_not_found"))
            except Exception:  # noqa: BLE001
                results.append(FileDownloadResponse(path=path, error="permission_denied"))
        return results


def _run_docker(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        args, capture_output=True, text=True, timeout=timeout
    )


async def create_container(folder: str) -> str:
    """Create and start a locked-down container with ``folder`` mounted read-only.

    Args:
        folder: Absolute host path to expose read-only at the configured mount path.

    Returns:
        The started container id.

    Raises:
        RuntimeError: If the container fails to start.
    """
    # Docker's bind mount source expects forward slashes even on Windows.
    mount_source = folder.replace("\\", "/")

    args: list[str] = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--network",
        "none",
        "--memory",
        settings.SANDBOX_MEMORY,
        "--cpus",
        settings.SANDBOX_CPUS,
        "--pids-limit",
        str(settings.SANDBOX_PIDS_LIMIT),
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        f"type=bind,source={mount_source},target={settings.SANDBOX_MOUNT_PATH},readonly",
        "-w",
        settings.SANDBOX_MOUNT_PATH,
    ]
    if getattr(settings, "SANDBOX_USER", ""):
        args += ["--user", settings.SANDBOX_USER]
    args += [settings.SANDBOX_IMAGE, "sleep", "infinity"]

    # First run may pull the image; allow extra time.
    proc = await asyncio.to_thread(_run_docker, args, 180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "docker run failed")
    return proc.stdout.strip()


async def remove_container(container_id: str) -> None:
    """Force-remove a sandbox container (best-effort)."""
    try:
        await asyncio.to_thread(_run_docker, ["docker", "rm", "-f", container_id], 30)
    except Exception:  # noqa: BLE001
        logger.warning("sandbox_container_force_remove_failed", container_id=container_id)
