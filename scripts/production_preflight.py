"""Fail-fast validation for the IONOS production environment file."""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings

_COMPOSE_ONLY = (
    "DOMAIN",
    "ACME_EMAIL",
    "WORKFLOWS_HOST_PATH",
    "MONGO_ROOT_USERNAME",
    "MONGO_ROOT_PASSWORD",
    "MONGO_APP_USERNAME",
    "MONGO_APP_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)


def parse_env(path: Path) -> dict[str, str]:
    """Parse the env.

    Args:
        path (Path): Filesystem path.

    Returns:
        dict[str, str]: The env.
    """
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid environment line: {raw_line!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    """Compute the main."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env.production"),
    )
    args = parser.parse_args()
    path = args.env_file
    if not path.is_file():
        raise SystemExit(f"Missing {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"{path} must not be readable by group/others; run chmod 600")
    values = parse_env(path)
    missing = [key for key in _COMPOSE_ONLY if not values.get(key)]
    if missing:
        raise SystemExit("Missing production variables: " + ", ".join(missing))
    try:
        config = Settings(_env_file=path)
    except ValidationError as exc:
        raise SystemExit(str(exc)) from exc

    workflow_path = Path(values["WORKFLOWS_HOST_PATH"])
    if workflow_path.exists() and not os.access(workflow_path, os.W_OK):
        raise SystemExit(f"{workflow_path} is not writable")
    print(
        "Production environment is valid for "
        f"https://{values['DOMAIN']} ({config.environment})."
    )


if __name__ == "__main__":
    main()
