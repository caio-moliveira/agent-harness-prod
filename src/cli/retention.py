"""Operator CLI for retention and erasure (#75).

    uv run python -m src.cli.retention purge              # apply the configured retention windows
    uv run python -m src.cli.retention erase --user 42    # LGPD erasure for one account
    uv run python -m src.cli.retention erase --user 42 --keep-account   # wipe history, keep login

Deletion is irreversible, so ``erase`` asks for confirmation unless ``--yes`` is passed (a cron
job or a runbook step would pass it; a human at a terminal should see the prompt).
"""

import argparse
import asyncio
import sys

from rich.console import Console

from src.app.core.retention import delete_user_data, purge_expired

_console = Console()


async def _run(args: argparse.Namespace) -> int:
    if args.command == "purge":
        report = await purge_expired()
        _console.print(f"[green]purge concluído[/green]: {report.as_dict()}")
    else:
        if not args.yes:
            _console.print(
                f"[yellow]Isto apaga permanentemente os dados do usuário {args.user}"
                f"{'' if args.keep_account else ' e a própria conta'}.[/yellow]"
            )
            if input("Digite 'apagar' para confirmar: ").strip().lower() != "apagar":
                _console.print("cancelado.")
                return 1
        report = await delete_user_data(args.user, delete_account=not args.keep_account)
        _console.print(f"[green]erasure concluído[/green]: {report.as_dict()}")
    if report.errors:
        _console.print(f"[red]{len(report.errors)} erro(s):[/red] {report.errors[:5]}")
        return 1
    return 0


def main() -> int:
    """Parse arguments and run the requested retention command."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("purge", help="apply the configured retention windows")
    erase = sub.add_parser("erase", help="erase one user's data (LGPD)")
    erase.add_argument("--user", type=int, required=True, help="user id")
    erase.add_argument("--keep-account", action="store_true", help="wipe data but keep the login")
    erase.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
