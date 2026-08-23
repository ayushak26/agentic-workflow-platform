"""Create or rotate a Mongo-backed local application user.

Run inside the application container so the production Mongo URI is reused:

    printf '%s' "$PASSWORD" | docker compose ... exec -T app \
      python scripts/manage_user.py upsert --username ayush --role admin --password-stdin
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.security.rbac import Role
from app.security.users import ensure_user_indexes, upsert_local_user


def parser() -> argparse.ArgumentParser:
    """Compute the parser.

    Returns:
        argparse.ArgumentParser: The result.
    """
    command = argparse.ArgumentParser(description="Manage Eurskem local users")
    sub = command.add_subparsers(dest="command", required=True)
    upsert = sub.add_parser("upsert", help="Create or rotate a user")
    upsert.add_argument("--username", required=True)
    upsert.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.CONSULTANT.value,
    )
    upsert.add_argument("--password-stdin", action="store_true")
    return command


async def run(args: argparse.Namespace) -> None:
    """Run the result.

    Args:
        args (argparse.Namespace): Positional arguments.
    """
    if args.password_stdin:
        password = sys.stdin.read().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match")
    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=5_000)
    try:
        db = client[settings.mongo_db]
        await db.command("ping")
        await ensure_user_indexes(db)
        await upsert_local_user(
            db,
            username=args.username,
            password=password,
            role=Role(args.role),
        )
    finally:
        client.close()
    print(f"User {args.username!r} is ready with role {args.role!r}.")


def main() -> None:
    """Compute the main."""
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
